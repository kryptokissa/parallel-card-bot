(function () {
  // Renders the character sheet from live state when the host page
  // provides it (wf:apply_state), and falls back to the sample sheet
  // otherwise. Strictly read-only: this page can never touch the
  // engine's gates or limits (Design Law 2).
  var LEVEL_XP = [0, 150, 400, 800, 1400, 2200, 3200, 4500];
  var TROPHY_ART = {
    first_blood: "🩸", walked_out: "💰", big_duck: "🦆", golden_mallard: "🥇",
    banded: "🎗️", storm_hunter: "🌩️", tough_bird: "🪶",
    back_from_the_bank: "🏦", marathon: "🏕️", clean_season: "🧹", old_dog: "🐕",
  };

  var state = {};

  function post(message) {
    try { window.parent && window.parent.postMessage(message, "*"); } catch (e) {}
  }

  function text(selector, value) {
    var el = document.querySelector(selector);
    if (el && value != null) el.textContent = String(value);
  }

  function render() {
    var game = (state && state.game) || {};
    var hunter = game.hunter || {};
    if (hunter.dog_name) text("[data-dog-name]", hunter.dog_name);
    if (hunter.level) {
      text("[data-level-title]",
        "Level " + hunter.level + " · " + (hunter.title || ""));
      var base = LEVEL_XP[hunter.level - 1] || 0;
      var next = LEVEL_XP[hunter.level] || (base + 1);
      var pct = Math.max(0, Math.min(100,
        Math.round(100 * ((hunter.xp || 0) - base) / (next - base))));
      var fill = document.querySelector("[data-xp-fill]");
      if (fill) fill.style.width = pct + "%";
      text("[data-xp-label]", (hunter.xp || 0) + " XP · " +
        (next > (hunter.xp || 0) ? (next - hunter.xp) + " to the next collar"
                                 : "top of the pack"));
    }
    text("[data-rating]", hunter.hunter_rating == null
      ? "Unranked" : "Rating " + hunter.hunter_rating);
    if (game.weather) text("[data-weather]", game.weather);
    var wall = document.querySelector("[data-trophy-wall]");
    if (wall && game.trophies && game.trophies.length) {
      wall.innerHTML = "";
      game.trophies.forEach(function (trophy) {
        var span = document.createElement("span");
        span.className = "trophy";
        span.title = (trophy.name || trophy.id) +
          (trophy.stat ? " · " + trophy.stat : "");
        span.textContent = TROPHY_ART[trophy.id] || "🏅";
        wall.appendChild(span);
      });
    }
    if (game.last_recap) text("[data-sample-recap]", game.last_recap);
  }

  window.addEventListener("message", function (event) {
    var msg = event.data;
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "wf:hello") {
      post({ type: "wf:hello_ack", version: "0.1" });
      post({ type: "wf:state", state: state });
      return;
    }
    if (msg.type === "wf:apply_state" && msg.state &&
        typeof msg.state === "object") {
      state = msg.state;
      render();
      post({ type: "wf:state", state: state });
    }
  });

  render();
})();
