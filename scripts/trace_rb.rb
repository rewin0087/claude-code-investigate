# Record how data changes, line by line, while Ruby code runs (Ruby 2.6+, no gems needed).
#
# Uses TracePoint to log, for app files only (under TRACE_ROOT, not gems / vendor):
#   * method calls with their arguments, return values and raised exceptions
#   * every executed line with the local variables / instance variables / ActiveRecord
#     attributes that line changed (before -> after)
#   * SQL issued through ActiveRecord (sql.active_record notifications) with bind values
#   * outgoing HTTP through Net::HTTP (Faraday, HTTParty and RestClient use it by default)
#
# Use it against the TEST environment, or a LOCAL DEVELOPMENT database inside a sandbox.
# While it records, outbound TCP to anything but loopback is blocked (TRACE_ALLOW_HOSTS=a,b or
# TRACE_ALLOW_NETWORK=1 to relax). Three ways to use it:
#
#   1. In a throwaway harness / spec, wrap exactly the scenario:
#        require "/abs/path/to/trace_rb.rb"
#        InvestigateTrace.record("SC1") { post "/users/sign_in", params: {...} }
#      -> writes $TRACE_DIR/SC1.json (default: <TRACE_ROOT>/.investigate/trace/SC1.json)
#
#   2. Run against real development data, rolled back afterwards (RAILS_ENV=development):
#        InvestigateTrace.sandbox("SC1", watch: { order: -> { Order.find(4512) } }) do
#          Refunds::Create.call(order_id: 4512, amount_cents: 1000)
#        end
#      -> one ActiveRecord transaction, rolled back at the end; watched records are reloaded
#         before and after (inside the transaction); ActionMailer / ActiveJob deliveries are
#         captured instead of sent; COMMITs that still reach the DB are reported.
#        TRACE_ROOT=$PWD TRACE_DIR=… TRACE_ENV=development RAILS_ENV=development bin/rails runner harness.rb
#
#   3. Trace a whole existing command:
#        TRACE_AUTOSTART=1 TRACE_OUT=/abs/out.json TRACE_ROOT=$PWD \
#          RUBYOPT="-r/abs/path/to/trace_rb.rb" RAILS_ENV=test bundle exec rspec spec/x_spec.rb:12
#
# Options (env): TRACE_ROOT, TRACE_DIR, TRACE_OUT, TRACE_ENTRY ("Class#method": record only
# while inside it), TRACE_FOCUS (comma-separated path fragments), TRACE_INCLUDE_TESTS=1,
# TRACE_MAX_EVENTS (default 20000), TRACE_NO_MASK=1 (don't redact secret-looking values).
#
# Structure: AppFiles (which files are app code), ValueFormatter (safe inspect + snapshots),
# Recorder (TracePoint callbacks + event log), NetHTTPHook (outgoing HTTP), NetworkGuard
# (outbound TCP), Sandbox (rolled-back transaction, watches, mail/job capture), and the
# InvestigateTrace module functions start / stop / record / sandbox / install_hooks.
require "json"
require "fileutils"

module InvestigateTrace
  MAX_REPR = 240
  SECRET_NAME = /(pass(wd|word)?|secret|token|api_?key|private_?key|authorization|cookie|session_?key|otp|cvv|encrypted)/i
  SECRET_IN_REPR = /((?:"|:|\b)\w*(?:pass(?:wd|word)?|secret|token|api_?key|authorization)\w*"?\s*(?:=>|:|=)\s*)"((?:\\.|[^"\\])*)"/i
  LIB_PATH = %r{/(vendor|node_modules|tmp)/|/gems/|/rubygems/|/ruby/\d|/\.rbenv/|/\.rvm/|/\.asdf/|/bundler/}
  TEST_PATH = %r{(^|/)(spec|test)/|_spec\.rb$|_test\.rb$}

  # Which files are app code (under TRACE_ROOT, not gems / vendor / tests) and their relative paths.
  class AppFiles
    attr_reader :root

    def initialize(root, include_tests, focus)
      @root = File.expand_path(root)
      @root = File.realpath(@root) rescue @root
      @include_tests = include_tests
      @focus = focus
      @cache = {}
    end

    def app?(path)
      return false if path.nil? || !path.start_with?("/")       # <internal:…>, (eval), -e
      @cache.fetch(path) do
        real = path
        inside = real.start_with?(@root + "/") ? real[@root.length..-1] : nil   # "/app/models/user.rb"
        ok = !inside.nil? && inside !~ LIB_PATH && !real.include?("trace_rb.rb")
        harness = real.include?("/.investigate/")
        ok &&= harness || @include_tests || inside !~ TEST_PATH
        ok &&= harness || @focus.empty? || @focus.any? { |f| inside.include?(f) }
        @cache[path] = ok
      end
    end

    def rel(path)
      path.to_s.start_with?(@root + "/") ? path[(@root.length + 1)..-1] : path.to_s
    end

    # [file, line, label] of the first app location in a backtrace.
    def caller_of(locations)
      loc = locations.find { |l| app?(l.absolute_path || l.path) }
      loc ? [rel(loc.absolute_path || loc.path), loc.lineno, loc.label] : [nil, nil, nil]
    end
  end

  # Safe, redacted, size-limited inspect (never runs a query), and frame snapshots.
  class ValueFormatter
    def initialize(mask)
      @mask = mask
    end

    def secret?(name)
      @mask && name.to_s =~ SECRET_NAME ? true : false
    end

    def safe(name, value)
      return '"[redacted]"' if @mask && name.to_s =~ SECRET_NAME && value.is_a?(String) && !value.empty?
      s = describe(value)
      s = s.gsub(SECRET_IN_REPR) { "#{$1}\"[redacted]\"" } if @mask
      s.length > MAX_REPR ? s[0, MAX_REPR - 1] + "…" : s
    rescue Exception => e # rubocop:disable Lint/RescueException
      "#<#{value.class rescue '?'} (inspect failed: #{e.class})>"
    end

    def describe(value)
      # Never let inspect run a query or load an association.
      if defined?(ActiveRecord::Relation) && value.is_a?(ActiveRecord::Relation)
        return value.loaded? ? "#<#{value.klass} relation (#{value.size} loaded)>" : "#<#{value.klass} relation not loaded: #{value.to_sql rescue '?'}>"
      end
      if defined?(ActiveRecord::Base) && value.is_a?(ActiveRecord::Base)
        attrs = value.attributes.map { |k, v| "#{k}: #{@mask && k =~ SECRET_NAME && v ? '[redacted]' : v.inspect}" }
        state = value.new_record? ? " new" : (value.destroyed? ? " destroyed" : "")
        errs = value.errors.any? ? " errors=#{value.errors.full_messages.inspect}" : ""
        return "#<#{value.class.name}#{state} #{attrs.join(', ')}#{errs}>"
      end
      if defined?(ActionController::Parameters) && value.is_a?(ActionController::Parameters)
        return "params#{value.to_unsafe_h.inspect}#{value.permitted? ? ' (permitted)' : ''}"
      end
      value.inspect
    end

    def snapshot(binding, obj)
      snap = {}
      binding.local_variables.each do |v|
        next if v.to_s.start_with?("_")
        put(snap, v.to_s, binding.local_variable_get(v))
      end
      if defined?(ActiveRecord::Base) && obj.is_a?(ActiveRecord::Base)
        obj.attributes.first(40).each { |k, v| snap["self.#{k}"] = safe(k, v) }
        snap["self.errors"] = obj.errors.full_messages.inspect if obj.errors.any?
      elsif !obj.is_a?(Module)
        obj.instance_variables.first(30).each do |iv|
          next if iv.to_s.start_with?("@_")
          put(snap, iv.to_s, obj.instance_variable_get(iv))
        end
      end
      snap
    rescue StandardError
      snap || {}
    end

    # ActiveRecord objects are shown attribute by attribute, so "@order.status: pending -> paid"
    # appears on the line that changed it instead of a whole new inspect string.
    def put(snap, name, value)
      if defined?(ActiveRecord::Base) && value.is_a?(ActiveRecord::Base)
        snap[name] = "#<#{value.class.name}#{value.new_record? ? ' new' : ''}>"
        value.attributes.first(40).each { |k, v| snap["#{name}.#{k}"] = safe(k, v) }
        snap["#{name}.errors"] = value.errors.full_messages.inspect if value.errors.any?
      else
        snap[name] = safe(name, value)
      end
    end
  end

  # TracePoint callbacks: calls, per-line changes, returns, raises, SQL and HTTP events.
  class Recorder
    attr_reader :events, :fmt, :sandboxes
    attr_accessor :sandbox

    def initialize(out)
      @out = out
      @files = AppFiles.new(ENV["TRACE_ROOT"] || Dir.pwd, ENV["TRACE_INCLUDE_TESTS"] == "1",
                            (ENV["TRACE_FOCUS"] || "").split(",").map(&:strip).reject(&:empty?))
      @fmt = ValueFormatter.new(ENV["TRACE_NO_MASK"] != "1")
      @max = (ENV["TRACE_MAX_EVENTS"] || 20_000).to_i
      @entry = ENV["TRACE_ENTRY"].to_s.strip
      @active = @entry.empty?
      @entry_depth = nil
      @events = []
      @stack = []
      @truncated = false
      @busy = false
      @paused = false
      @sandbox = nil
      @sandboxes = []
      @blocked = 0
      @started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    end

    def emit(ev)
      return if !@active || @paused
      if @events.size >= @max
        @truncated = true
        return
      end
      ev["i"] = @events.size + 1
      ev["t"] = ((Process.clock_gettime(Process::CLOCK_MONOTONIC) - @started) * 1000).round(2)
      @events << ev
    end

    def owner(tp)
      k = tp.defined_class
      k = k.to_s.sub(/^#<Class:(.+)>$/, '\1.') if k.to_s.start_with?("#<Class:")
      k.is_a?(String) ? "#{k}#{tp.method_id}" : "#{k}##{tp.method_id}"
    rescue StandardError
      tp.method_id.to_s
    end

    def app_caller
      @files.caller_of(caller_locations(2, 80))
    end

    def guard
      return if @busy
      @busy = true
      yield
    ensure
      @busy = false
    end

    def on_call(tp)
      return unless @files.app?(tp.path)
      name = owner(tp)
      if !@active && @entry != "" && (name == @entry || name.end_with?(@entry) || tp.method_id.to_s == @entry)
        @active = true
        @entry_depth = @stack.size
      end
      b = tp.binding
      snap = @fmt.snapshot(b, tp.self)
      params = (tp.defined_class.instance_method(tp.method_id).parameters.map { |_t, n| n.to_s } rescue [])
      args = snap.select { |k, _| params.include?(k) }
      @stack << { path: tp.path, mid: tp.method_id, line: nil, snap: snap }
      emit("ev" => "call", "file" => @files.rel(tp.path), "line" => tp.lineno, "fn" => name, "depth" => @stack.size, "args" => args)
    end

    def on_line(tp)
      return unless @files.app?(tp.path)
      top = @stack.last
      if top.nil? || top[:path] != tp.path || top[:mid] != tp.method_id
        found = @stack.rindex { |f| f[:path] == tp.path && f[:mid] == tp.method_id }
        if found
          top = @stack[found]
        else
          top = { path: tp.path, mid: tp.method_id, line: nil, snap: {}, implicit: true }
          @stack << top
        end
      end
      new = @fmt.snapshot(tp.binding, tp.self)
      if top[:line]
        changes = {}
        new.each { |k, v| changes[k] = [top[:snap][k], v] if top[:snap][k] != v }
        ev = { "ev" => "line", "file" => @files.rel(tp.path), "line" => top[:line], "fn" => tp.method_id.to_s, "depth" => @stack.size }
        ev["changes"] = changes unless changes.empty?
        emit(ev)
      end
      top[:snap] = new
      top[:line] = tp.lineno
    end

    def on_return(tp)
      return unless @files.app?(tp.path)
      @stack.pop while @stack.last && @stack.last[:implicit] && !(@stack.last[:path] == tp.path && @stack.last[:mid] == tp.method_id)
      top = @stack.last
      if top && top[:path] == tp.path && top[:mid] == tp.method_id
        new = @fmt.snapshot(tp.binding, tp.self)
        changes = {}
        new.each { |k, v| changes[k] = [top[:snap][k], v] if top[:snap][k] != v }
        if top[:line]
          ev = { "ev" => "line", "file" => @files.rel(tp.path), "line" => top[:line], "fn" => tp.method_id.to_s, "depth" => @stack.size }
          ev["changes"] = changes unless changes.empty?
          emit(ev)
        end
        @stack.pop
      end
      emit("ev" => "return", "file" => @files.rel(tp.path), "line" => tp.lineno, "fn" => owner(tp), "depth" => @stack.size + 1,
           "value" => @fmt.safe(:return, tp.return_value))
      if @entry_depth && @stack.size <= @entry_depth
        @active = false
        @entry_depth = nil
      end
    end

    def on_raise(tp)
      e = tp.raised_exception
      path = @files.app?(tp.path) ? tp.path : nil
      origin = path ? "app" : "library"
      file, line, fn = path ? [@files.rel(path), tp.lineno, tp.method_id.to_s] : app_caller
      return unless file
      # Library-internal raises while files load (require / autoload) are noise.
      return if origin == "library" && fn.to_s.start_with?("<")
      last = @events.last
      # The same failure re-raised / wrapped inside the library: keep the first one.
      return if origin == "library" && last && last["ev"] == "exception" && last["file"] == file && last["line"] == line
      emit("ev" => "exception", "file" => file, "line" => line, "fn" => fn, "depth" => @stack.size,
           "origin" => origin, "type" => e.class.name, "message" => @fmt.safe(:message, e.message.to_s).sub(/\A"/, "").sub(/"\z/, ""))
    end

    def on_sql(payload)
      return unless @active
      name = payload[:name].to_s
      if payload[:sql].to_s =~ /\A\s*COMMIT\b/i
        file, line, fn = app_caller
        emit("ev" => "commit", "file" => file, "line" => line, "fn" => fn, "via" => "active_record")
        @sandbox.commits += 1 if @sandbox
        return
      end
      return if name == "SCHEMA" || payload[:sql].to_s =~ /\A\s*(BEGIN|ROLLBACK|SAVEPOINT|RELEASE)/i
      file, line, fn = app_caller
      return unless file
      binds = (payload[:type_casted_binds] || []) rescue []
      binds = binds.call if binds.respond_to?(:call)
      keys = (payload[:binds] || []).map { |b| b.respond_to?(:name) ? b.name : nil } rescue []
      shown = binds.each_with_index.map { |v, i| @fmt.secret?(keys[i]) ? "[redacted]" : v }
      emit("ev" => "sql", "file" => file, "line" => line, "fn" => fn, "sql" => payload[:sql].to_s.strip[0, 2000],
           "op" => payload[:sql].to_s.strip.split(/\s+/).first.to_s.upcase, "name" => name,
           "params" => shown.empty? ? nil : @fmt.safe(:params, shown), "via" => "active_record")
    end

    def on_http(http, req, res, error)
      return if !@active
      file, line, fn = app_caller
      return unless file
      ev = { "ev" => "http", "file" => file, "line" => line, "fn" => fn, "method" => req.method,
             "url" => "#{http.use_ssl? ? 'https' : 'http'}://#{http.address}#{req.path}", "via" => "net_http" }
      ev["status"] = res.code.to_i if res
      ev["error"] = "#{error.class}: #{error.message}"[0, 300] if error
      emit(ev)
    end

    # Evaluate without recording (watch queries are not part of the scenario).
    def quietly
      was = @paused
      @paused = true
      yield
    ensure
      @paused = was
    end

    def on_blocked(host, port)
      @blocked += 1
      file, line, fn = app_caller
      emit("ev" => "http", "file" => file, "line" => line, "fn" => fn, "method" => "CONNECT", "url" => "#{host}:#{port}",
           "blocked" => true, "error" => "blocked by investigate: outbound network is off (TRACE_ALLOW_HOSTS=#{host})")
    end

    def write(status, error = nil)
      data = { "tool" => "trace_rb", "version" => 1, "root" => @files.root, "status" => status,
               "duration_ms" => ((Process.clock_gettime(Process::CLOCK_MONOTONIC) - @started) * 1000).round(1),
               "truncated" => @truncated, "events" => @events }
      data["error"] = error if error
      data["environment"] = ENV["TRACE_ENV"] if ENV["TRACE_ENV"]
      data["sandboxes"] = @sandboxes unless @sandboxes.empty?
      data["network_blocked"] = @blocked if @blocked > 0
      FileUtils.mkdir_p(File.dirname(@out))
      File.write(@out, JSON.generate(data))
      count = ->(k) { @events.count { |e| e["ev"] == k } }
      changed = @events.count { |e| e["changes"] }
      warn "[trace_rb] #{status}: #{@events.size} events (#{count.('call')} calls, #{changed} lines with data changes, " \
           "#{count.('sql')} sql, #{count.('http')} http)#{@truncated ? ' TRUNCATED' : ''} -> #{@out}"
    end
  end

  # Refuses outbound TCP connections except loopback / TRACE_ALLOW_HOSTS while recording.
  # (C database drivers such as pg / mysql2 don't go through these Ruby sockets.)
  module NetworkGuard
    LOOPBACK = %w[localhost 127.0.0.1 ::1 0.0.0.0 ip6-localhost].freeze

    def self.allowed?(host)
      h = host.to_s.downcase.delete("[]")
      return true if ENV["TRACE_ALLOW_NETWORK"] == "1" || LOOPBACK.include?(h) || h.start_with?("127.")
      return true if (ENV["TRACE_ALLOW_HOSTS"] || "").split(",").map(&:strip).include?(h)
      require "resolv"
      addrs = Resolv.getaddresses(h) rescue []
      !addrs.empty? && addrs.all? { |a| a.start_with?("127.") || a == "::1" }
    end

    def self.check!(host, port)
      rec = InvestigateTrace.current
      return if rec.nil? || host.nil? || allowed?(host)
      rec.guard { rec.on_blocked(host, port) }
      raise Errno::ECONNREFUSED, "investigate: outbound connection to #{host}:#{port} blocked"
    end

    module TCPSocketHook
      def initialize(*args, &block)
        NetworkGuard.check!(args[0], args[1])
        super
      end
      ruby2_keywords(:initialize) if respond_to?(:ruby2_keywords, true)
    end

    module SocketTcpHook
      def tcp(host, port, *args, &block)
        NetworkGuard.check!(host, port)
        super
      end
      ruby2_keywords(:tcp) if respond_to?(:ruby2_keywords, true)
    end

    def self.install
      return if @installed
      require "socket"
      TCPSocket.prepend(TCPSocketHook)
      Socket.singleton_class.prepend(SocketTcpHook)
      @installed = true
    end
  end

  # One scenario against real (development) data: an ActiveRecord transaction that is rolled
  # back, watched records before/after, ActionMailer / ActiveJob deliveries captured.
  class Sandbox
    attr_accessor :commits
    attr_reader :result

    def initialize(rec, name, watch)
      @rec, @watch = rec, watch || {}
      @commits = 0
      @result = { "name" => name, "rollback" => defined?(ActiveRecord::Base) ? "active_record" : "none",
                  "rolled_back" => false, "watch" => [], "emails" => [], "jobs" => [] }
    end

    def run
      before = snapshot
      capture_side_effects do
        if defined?(ActiveRecord::Base)
          @in_transaction = true
          ActiveRecord::Base.transaction(requires_new: true) do
            begin
              yield
            ensure
              finish(before)
            end
            raise ActiveRecord::Rollback
          end
        else
          begin
            yield
          ensure
            finish(before)
          end
        end
      end
    ensure
      @result["rolled_back"] = true if @in_transaction     # by ActiveRecord::Rollback, or by the exception
      @result["commits"] = @commits
      @result["warning"] = "the code committed #{@commits} transaction(s) inside the sandbox: those changes were NOT rolled back" if @commits > 0
      @result["warning"] ||= "no ActiveRecord: writes made by the scenario persist" unless defined?(ActiveRecord::Base)
      @result["note"] = "after_commit callbacks and jobs enqueued after commit do not run inside a rolled-back transaction"
    end

    def finish(before)
      after = snapshot     # still inside the transaction: shows what the scenario wrote
      @watch.each_key do |k|
        @result["watch"] << { "name" => k.to_s, "before" => before[k], "after" => after[k], "changes" => diff(before[k], after[k]) }
      end
    end

    def snapshot
      out = {}
      @watch.each do |k, fn|
        out[k] = begin
          @rec.quietly { plain(fn.call) }
        rescue StandardError => e
          "<watch failed: #{e.class}: #{e.message}>"
        end
      end
      out
    end

    def plain(v, depth = 0)
      case v
      when nil, true, false, Numeric, String then v
      when Symbol then v.to_s
      when Hash then v.first(60).map { |k, x| [k.to_s, @rec.fmt.secret?(k) && x ? "[redacted]" : plain(x, depth + 1)] }.to_h
      when Array then depth < 4 ? v.first(30).map { |x| plain(x, depth + 1) } : "[…]"
      else
        if defined?(ActiveRecord::Relation) && v.is_a?(ActiveRecord::Relation)
          v.limit(30).to_a.map { |r| plain(r, depth + 1) }
        elsif defined?(ActiveRecord::Base) && v.is_a?(ActiveRecord::Base)
          r = (v.persisted? ? (v.class.find_by(v.class.primary_key => v.id) || v) : v)
          plain(r.attributes, depth + 1)
        else
          @rec.fmt.safe(:value, v)
        end
      end
    end

    def diff(b, a, prefix = "")
      if b.is_a?(Hash) && a.is_a?(Hash)
        (b.keys | a.keys).flat_map { |k| diff(b[k], a[k], "#{prefix}#{k}.") }
      elsif b.is_a?(Array) && a.is_a?(Array) && b.size == a.size
        b.each_with_index.flat_map { |x, i| diff(x, a[i], "#{prefix}#{i}.") }
      else
        b == a ? [] : [{ "path" => prefix.chomp(".").empty? ? "value" : prefix.chomp("."), "before" => b, "after" => a }]
      end
    end

    def capture_side_effects
      mail_state = capture_mail
      job_adapter = capture_jobs
      yield
    ensure
      collect_mail(mail_state)
      collect_jobs(job_adapter)
    end

    def capture_mail
      return nil unless defined?(ActionMailer::Base)
      state = [ActionMailer::Base.delivery_method, ActionMailer::Base.perform_deliveries]
      ActionMailer::Base.delivery_method = :test
      ActionMailer::Base.perform_deliveries = true
      @mail_start = ActionMailer::Base.deliveries.size
      state
    end

    def collect_mail(state)
      return unless state
      ActionMailer::Base.deliveries[@mail_start..-1].to_a.each do |m|
        @result["emails"] << { "subject" => m.subject.to_s, "to" => Array(m.to).size }
      end
      ActionMailer::Base.delivery_method, ActionMailer::Base.perform_deliveries = state
    end

    def capture_jobs
      return nil unless defined?(ActiveJob::Base)
      original = ActiveJob::Base.queue_adapter
      ActiveJob::Base.queue_adapter = :test
      original
    end

    def collect_jobs(original)
      return if original.nil?
      ActiveJob::Base.queue_adapter.enqueued_jobs.each do |j|
        @result["jobs"] << { "job" => (j[:job] || j["job_class"]).to_s, "queue" => (j[:queue] || j["queue_name"]).to_s }
      end
      ActiveJob::Base.queue_adapter = original
    end
  end

  module NetHTTPHook
    def request(req, body = nil, &block)
      rec = InvestigateTrace.current
      return super unless rec
      res = nil
      begin
        res = super
      rescue Exception => e # rubocop:disable Lint/RescueException
        rec.guard { rec.on_http(self, req, nil, e) }
        raise
      end
      rec.guard { rec.on_http(self, req, res, nil) }
      res
    end
  end

  class << self
    attr_reader :current

    def start(out)
      install_hooks
      @current = Recorder.new(out)
      rec = @current
      @tp = TracePoint.new(:call, :return, :line, :raise) do |tp|
        install_hooks unless @sql_hooked && @http_hooked     # Rails may load after this file
        rec.guard do
          case tp.event
          when :call then rec.on_call(tp)
          when :line then rec.on_line(tp)
          when :return then rec.on_return(tp)
          when :raise then rec.on_raise(tp)
          end
        end
      end
      @tp.enable
      rec
    end

    def stop(status = "ok", error = nil)
      @tp.disable if @tp
      rec = @current
      @current = nil
      @tp = nil
      rec.write(status, error) if rec
      rec
    end

    def default_dir
      ENV["TRACE_DIR"] || (ENV["TRACE_OUT"] && File.dirname(ENV["TRACE_OUT"])) ||
        File.join(File.expand_path(ENV["TRACE_ROOT"] || Dir.pwd), ".investigate", "trace")
    end

    # Trace exactly the block; writes <TRACE_DIR>/<name>.json and returns the block's value.
    def record(name, out: nil)
      out ||= File.join(default_dir, "#{name.to_s.gsub(/[^\w.-]+/, '_')}.json")
      FileUtils.mkdir_p(File.dirname(out))
      start(out)
      begin
        result = yield
      rescue Exception => e # rubocop:disable Lint/RescueException
        stop("exception", "type" => e.class.name, "message" => e.message.to_s[0, 1000])
        raise
      end
      stop("ok")
      result
    end

    # Run the block against real (development) data, rolled back afterwards; records <name>.json.
    def sandbox(name, watch: {}, out: nil)
      record(name, out: out) do
        sb = Sandbox.new(current, name, watch)
        current.sandbox = sb
        begin
          result = nil
          sb.run { result = yield }
          result
        ensure
          current.sandbox = nil
          current.sandboxes << sb.result
        end
      end
    end

    def install_hooks
      NetworkGuard.install unless ENV["TRACE_ALLOW_NETWORK"] == "1"
      if !@sql_hooked && defined?(ActiveSupport::Notifications)
        ActiveSupport::Notifications.subscribe("sql.active_record") do |*args|
          rec = InvestigateTrace.current
          rec.guard { rec.on_sql(args.last) } if rec
        end
        @sql_hooked = true
      end
      if !@http_hooked && defined?(Net::HTTP)
        Net::HTTP.prepend(NetHTTPHook) unless Net::HTTP < NetHTTPHook
        @http_hooked = true
      end
    end
  end
end

if ENV["TRACE_AUTOSTART"] == "1" && !$__investigate_trace_started
  $__investigate_trace_started = true
  out = ENV["TRACE_OUT"] || File.join(InvestigateTrace.default_dir, "trace.json")
  FileUtils.mkdir_p(File.dirname(out))
  InvestigateTrace.start(out)
  at_exit do
    err = $!
    status = err && !err.is_a?(SystemExit) ? "exception" : "ok"
    rec = InvestigateTrace.current
    InvestigateTrace.stop(status, err && !err.is_a?(SystemExit) ? { "type" => err.class.name, "message" => err.message.to_s[0, 1000] } : nil) if rec && rec.events.any?
  end
end
