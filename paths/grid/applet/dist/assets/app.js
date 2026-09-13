(function () {
  // Read-only view of the grid the path decided on. It renders state the host
  // provides and can never change a gate, a limit, or an order — the applet has
  // no path into the engine at all.
  //
  // Host messaging is origin-locked: the parent's origin is captured from the
  // first wf:hello, every later message must arrive from that same origin and
  // from the parent window itself, and every reply targets that captured origin
  // explicitly. No message is ever sent to a wildcard target origin, and
  // nothing is sent at all before a handshake establishes who the parent is.
  var state = {};
  var parentOrigin = null;

  function isValidOrigin(origin) {
    return typeof origin === "string" && /^https?:\/\//.test(origin);
  }

  function post(message) {
    if (!parentOrigin) return; // no handshake yet: say nothing
    try {
      if (window.parent && window.parent !== window) {
        window.parent.postMessage(message, parentOrigin);
      }
    } catch (e) {}
  }

  function text(selector, value) {
    var el = document.querySelector(selector);
    if (el && value != null) el.textContent = String(value);
  }

  function show(selector, visible) {
    var el = document.querySelector(selector);
    if (el) el.hidden = !visible;
  }

  function money(value) {
    var n = Number(value);
    if (!isFinite(n)) return "—";
    return "$" + n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  function renderRungs(rungs) {
    var body = document.querySelector("[data-rungs]");
    if (!body) return;
    body.textContent = "";
    if (!rungs || !rungs.length) {
      show("[data-rungs-empty]", true);
      return;
    }
    show("[data-rungs-empty]", false);
    rungs.forEach(function (rung) {
      var row = document.createElement("div");
      row.className = "rung " + (rung.side === "buy" ? "buy" : "sell");
      var side = document.createElement("span");
      side.className = "side";
      side.textContent = rung.side === "buy" ? "buy" : "sell";
      var price = document.createElement("span");
      price.className = "price";
      price.textContent = rung.price == null ? "—" : String(rung.price);
      var size = document.createElement("span");
      size.className = "size";
      size.textContent = rung.size == null ? "" : String(rung.size);
      var notional = document.createElement("span");
      notional.className = "notional";
      notional.textContent = rung.notional_usd == null ? "" : money(rung.notional_usd);
      row.appendChild(side);
      row.appendChild(price);
      row.appendChild(size);
      row.appendChild(notional);
      body.appendChild(row);
    });
  }

  function renderGates(gates) {
    var list = document.querySelector("[data-gates]");
    if (!list) return;
    list.textContent = "";
    if (!gates || !gates.length) {
      show("[data-gates-section]", false);
      return;
    }
    show("[data-gates-section]", true);
    gates.forEach(function (gate) {
      var row = document.createElement("div");
      row.className = "gate " + (gate.passed ? "pass" : "fail");
      var mark = document.createElement("span");
      mark.className = "mark";
      mark.textContent = gate.passed ? "pass" : "fail";
      var name = document.createElement("span");
      name.className = "gname";
      name.textContent = String(gate.name || "");
      var detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = String(gate.detail || "");
      row.appendChild(mark);
      row.appendChild(name);
      row.appendChild(detail);
      list.appendChild(row);
    });
  }

  function render() {
    var config = state.config || {};
    var status = state.status || "awaiting_interview";

    text("[data-market]", config.market || "no market yet");
    text("[data-status]", status.replace(/_/g, " "));

    var hasRange = config.lower != null && config.upper != null;
    show("[data-config-section]", hasRange);
    if (hasRange) {
      text("[data-range]", config.lower + " – " + config.upper);
      text("[data-levels]", config.levels == null ? "—" : config.levels);
      text("[data-spacing]", config.spacing || "—");
      text("[data-leverage]", config.leverage == null ? "—" : config.leverage + "x");
      text("[data-breakout]", (config.breakout || "unanswered").replace(/_/g, " "));
      text("[data-capital]", money(config.capital_usd));
    }

    // A grid that has not answered the breakout question cannot start, so say
    // so rather than showing a range that will never be traded.
    show("[data-breakout-warning]", !config.breakout);

    renderRungs(state.rungs);
    renderGates(state.gates);

    var summary = state.summary || (state.decision && state.decision.summary);
    show("[data-summary-section]", !!summary);
    if (summary) text("[data-summary]", summary);

    var adjustments = state.adjustments || [];
    show("[data-clamp-section]", adjustments.length > 0);
    var clamps = document.querySelector("[data-clamps]");
    if (clamps) {
      clamps.textContent = "";
      adjustments.forEach(function (note) {
        var li = document.createElement("li");
        li.textContent = String(note);
        clamps.appendChild(li);
      });
    }

    var daily = state.daily_loss;
    show("[data-daily-section]", !!daily);
    if (daily) text("[data-daily]", String(daily));
  }

  window.addEventListener("message", function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== "object") return;

    if (msg.type === "wf:hello") {
      // First valid hello wins; later hellos must match it.
      if (!parentOrigin) {
        if (!isValidOrigin(event.origin)) return;
        if (event.source !== window.parent) return;
        parentOrigin = event.origin;
      }
      if (event.origin !== parentOrigin) return;
      post({ type: "wf:hello_ack", version: "0.1" });
      post({ type: "wf:state", state: state });
      return;
    }

    // Everything else is ignored until the handshake, and afterwards only
    // accepted from the captured parent origin and the parent window.
    if (!parentOrigin || event.origin !== parentOrigin) return;
    if (event.source !== window.parent) return;

    if (msg.type === "wf:apply_state" && msg.state && typeof msg.state === "object") {
      state = msg.state;
      render();
      post({ type: "wf:state", state: state });
      return;
    }
  });

  render();
})();
