/* Assay applet — renders a diligence report document.
 *
 * Read-only. The page holds no keys, makes no network calls, and asks for
 * no wallet. It renders whatever report the host hands it through
 * `wf:apply_state`, and falls back to a clearly-labelled synthetic example
 * so the layout is legible before a real run exists.
 *
 * Messaging is origin-locked: the origin is learned from the host's
 * `wf:hello` and every later post goes only there.
 */
(function () {
  "use strict";

  var hostOrigin = null;
  var state = { report: window.__ASSAY_EXAMPLE__ || null };

  function post(message) {
    if (!hostOrigin) return;
    try {
      window.parent.postMessage(message, hostOrigin);
    } catch (err) {
      /* the host went away; nothing to do */
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function kv(key, value, mono) {
    var box = el("div", "kv");
    box.appendChild(el("div", "k", key));
    var v = el("div", mono ? "v mono" : "v", value === null || value === undefined || value === "" ? "unresolved" : value);
    if (value === null || value === undefined || value === "") v.classList.add("faint");
    box.appendChild(v);
    return box;
  }

  function pill(text, kind) {
    return el("span", "pill " + String(kind || "").toLowerCase().replace(/[^a-z]/g, "_"), text);
  }

  function metadataValue(entry) {
    if (!entry) return "";
    if (entry.state === "resolved") return entry.value;
    return "";
  }

  /* Base-unit amounts arrive as decimal strings so nothing is lost to
   * JavaScript's 53-bit integers. Format them by string surgery, never by
   * dividing a Number. */
  function formatUnits(amount, decimals) {
    var text = String(amount === null || amount === undefined ? "" : amount);
    if (!/^-?\d+$/.test(text)) return text;
    var places = Number(decimals);
    if (!Number.isFinite(places) || places <= 0) return text;
    var sign = text.charAt(0) === "-" ? "-" : "";
    var digits = sign ? text.slice(1) : text;
    if (digits.length <= places) digits = new Array(places - digits.length + 2).join("0") + digits;
    var whole = digits.slice(0, digits.length - places);
    var fraction = digits.slice(digits.length - places).replace(/0+$/, "").slice(0, 6);
    var grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return sign + grouped + (fraction ? "." + fraction : "");
  }

  function shortHash(value) {
    if (!value || value.length < 18) return value || "";
    return value.slice(0, 10) + "…" + value.slice(-6);
  }

  function renderTarget(report) {
    var card = el("div", "card");
    var head = el("div", "spread");
    var left = el("div");
    var title = el("h1", null, metadataValue(report.target.metadata.name) || "Unresolved name");
    left.appendChild(title);
    var sub = el("div", "mono muted", report.target.caip10);
    left.appendChild(sub);
    head.appendChild(left);

    var right = el("div");
    var verdict = report.verdict || {};
    right.appendChild(pill(verdict.call || "no verdict", verdict.call || "not_checked"));
    head.appendChild(right);
    card.appendChild(head);

    if (verdict.statement) {
      var statement = el("p", null, verdict.statement);
      statement.style.marginBottom = "0";
      card.appendChild(statement);
    }

    var grid = el("div", "grid");
    grid.style.marginTop = "14px";
    var pinKeys = Object.keys(report.pins || {});
    var pin = pinKeys.length ? report.pins[pinKeys[0]] : null;
    grid.appendChild(kv("Symbol", metadataValue(report.target.metadata.symbol)));
    grid.appendChild(kv("Decimals", metadataValue(report.target.metadata.decimals)));
    var rawSupply = metadataValue(report.target.metadata.total_supply);
    var supplyDecimals = metadataValue(report.target.metadata.decimals);
    grid.appendChild(
      kv(
        "Total supply",
        rawSupply === "" ? "" : formatUnits(rawSupply, supplyDecimals) + (supplyDecimals === "" ? " base units" : ""),
        true
      )
    );
    grid.appendChild(kv("Block", pin ? pin.number : "", true));
    grid.appendChild(kv("Block hash", pin ? shortHash(pin.block_hash) : "", true));
    grid.appendChild(kv("Pinned at (UTC)", pin ? pin.utc : "", true));
    grid.appendChild(kv("Runtime hash", shortHash(report.target.runtime && report.target.runtime.hash), true));
    grid.appendChild(
      kv("Proxy", report.target.proxy && report.target.proxy.is_proxy ? report.target.proxy.pattern : "none detected")
    );
    card.appendChild(grid);

    var unresolved = report.target.unresolved_metadata || [];
    if (unresolved.length) {
      card.appendChild(
        el("div", "faint", "Unresolved metadata: " + unresolved.join(", ") + " — recorded as unresolved, never defaulted.")
      );
    }
    return card;
  }

  function renderRatings(report) {
    var card = el("div", "card");
    card.appendChild(el("h2", null, "Surfaces"));
    var wrap = el("div", "overflow");
    var table = el("table");
    var head = el("tr");
    ["Surface", "Status", "Severity", "Confidence", "Coverage", "Time basis"].forEach(function (label) {
      head.appendChild(el("th", null, label));
    });
    table.appendChild(head);
    (report.ratings || []).forEach(function (rating) {
      var tr = el("tr");
      tr.appendChild(el("td", null, rating.dimension.replace(/_/g, " ")));
      var statusCell = el("td");
      statusCell.appendChild(pill(rating.status.replace(/_/g, " "), rating.status));
      tr.appendChild(statusCell);
      // A surface that was never examined has no meaningful severity or
      // confidence; showing them at full weight reads as a result.
      var idle = rating.status === "not_checked" || rating.status === "not_applicable";
      tr.appendChild(el("td", idle ? "dim" : "muted", rating.severity));
      tr.appendChild(el("td", idle ? "dim" : "muted", rating.confidence));
      tr.appendChild(el("td", "faint", rating.coverage || "—"));
      tr.appendChild(el("td", "faint", rating.time_basis || "—"));
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    card.appendChild(wrap);
    card.appendChild(
      el(
        "div",
        "faint",
        "Rated separately on purpose. A critical finding is never averaged away against unrelated clean checks."
      )
    );
    return card;
  }

  function renderFindings(report) {
    var card = el("div", "card");
    card.appendChild(el("h2", null, "Finding-to-evidence ledger"));
    var findings = report.findings || [];
    if (!findings.length) {
      card.appendChild(el("div", "faint", "No findings recorded."));
      return card;
    }
    findings.forEach(function (finding) {
      var box = el("details");
      var summary = el("summary");
      var label = el("span", null, finding.finding_id + " · " + finding.proposition.slice(0, 96) + (finding.proposition.length > 96 ? "…" : ""));
      summary.appendChild(label);
      summary.appendChild(pill(finding.status.replace(/_/g, " "), finding.status));
      box.appendChild(summary);

      box.appendChild(el("p", null, finding.proposition));
      var meta = el("div", "faint");
      meta.textContent =
        "chain " + finding.chain_id + " · " + finding.address + " · confidence " + finding.confidence + " · severity " + finding.severity;
      box.appendChild(meta);

      if (finding.decoding_basis) box.appendChild(el("div", "faint", "Decoding basis: " + finding.decoding_basis));
      if (finding.coverage) box.appendChild(el("div", "faint", "Coverage: " + finding.coverage));
      if (finding.stale_when) box.appendChild(el("div", "faint", "Stale when: " + finding.stale_when));
      (finding.alternatives || []).forEach(function (alternative) {
        box.appendChild(el("div", "faint", "Alternative reading: " + alternative));
      });
      if (finding.notes) box.appendChild(el("div", "faint", finding.notes));

      if ((finding.evidence || []).length) {
        var list = el("ul", "evidence");
        finding.evidence.forEach(function (item) {
          var li = el("li");
          li.appendChild(el("span", "tier", "[" + item.tier + "] "));
          li.appendChild(document.createTextNode(item.summary + (item.query ? " — " + item.query : "")));
          list.appendChild(li);
        });
        box.appendChild(list);
      } else {
        box.appendChild(el("div", "faint", "No evidence rows attached."));
      }
      card.appendChild(box);
    });
    return card;
  }

  function renderLimitations(report) {
    var limitations = report.coverage_limitations || [];
    if (!limitations.length) return null;
    var card = el("div", "card");
    card.appendChild(el("h2", null, "Coverage limitations"));
    card.appendChild(
      el("div", "faint", "Limits of the run, not properties of the token. Nothing is rated clear on the strength of these.")
    );
    limitations.forEach(function (limitation) {
      var box = el("div", "limitation");
      box.appendChild(el("div", null, limitation.scope));
      box.appendChild(el("div", "faint", limitation.reason));
      if (limitation.attempted) box.appendChild(el("div", "faint", "Attempted: " + limitation.attempted));
      if (limitation.consequence) box.appendChild(el("div", "faint", "Consequence: " + limitation.consequence));
      card.appendChild(box);
    });
    return card;
  }

  function renderClosing(report) {
    var verdict = report.verdict || {};
    var card = el("div", "card");
    card.appendChild(el("h2", null, "What would change this"));
    var lists = [
      ["Main reasons", verdict.main_reasons],
      ["Strongest contrary evidence", verdict.strongest_contrary_evidence],
      ["Unresolved questions", verdict.unresolved_questions],
      ["Would change the verdict", verdict.would_change_this]
    ];
    lists.forEach(function (pair) {
      var items = pair[1] || [];
      if (!items.length) return;
      card.appendChild(el("div", "muted", pair[0]));
      var list = el("ul", "evidence");
      items.forEach(function (item) {
        list.appendChild(el("li", null, item));
      });
      card.appendChild(list);
    });
    card.appendChild(
      el(
        "div",
        "scopenote",
        "This page renders a report document. It performs no reads of its own, holds no keys, and asks for no wallet."
      )
    );
    return card;
  }

  function render() {
    var app = document.getElementById("app");
    app.textContent = "";
    var report = state.report;
    if (!report || !report.target) {
      var empty = el("div", "card");
      empty.appendChild(el("h1", null, "Assay"));
      empty.appendChild(
        el("div", "muted", "No report loaded. Run a diligence pass and hand the report document to this page.")
      );
      app.appendChild(empty);
      return;
    }
    if (report.synthetic) {
      app.appendChild(el("div", "banner", report.synthetic_notice || "Synthetic example — not a live finding."));
    }
    app.appendChild(renderTarget(report));
    app.appendChild(renderRatings(report));
    var limitations = renderLimitations(report);
    if (limitations) app.appendChild(limitations);
    app.appendChild(renderFindings(report));
    app.appendChild(renderClosing(report));
  }

  window.addEventListener("message", function (event) {
    var message = event.data;
    if (!message || typeof message !== "object") return;

    if (message.type === "wf:hello") {
      hostOrigin = event.origin;
      post({ type: "wf:hello_ack", version: "0.1" });
      post({ type: "wf:state", state: state });
      return;
    }
    if (!hostOrigin || event.origin !== hostOrigin) return;

    if (message.type === "wf:apply_state" && message.state && typeof message.state === "object") {
      state = { report: message.state.report || null };
      render();
      post({ type: "wf:state", state: state });
    }
  });

  render();
})();
