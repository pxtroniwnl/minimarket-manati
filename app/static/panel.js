// Tiempo real del panel: SSE nativo → refresca la vista con htmx; suena y vibra con pedidos nuevos.
(function () {
  var audioCtx = null;
  var soundBtn = document.getElementById("sound-btn");
  var wantSound = false;
  try { wantSound = localStorage.getItem("sound") === "1"; } catch (e) {}

  function unlockAudio() {
    if (audioCtx) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) { audioCtx = null; }
  }

  function tone(freq, start, dur) {
    var o = audioCtx.createOscillator();
    var g = audioCtx.createGain();
    o.type = "sine";
    o.frequency.value = freq;
    o.connect(g);
    g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.25, audioCtx.currentTime + start);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + start + dur);
    o.start(audioCtx.currentTime + start);
    o.stop(audioCtx.currentTime + start + dur);
  }

  function ring() {
    if (audioCtx && wantSound) {
      if (audioCtx.state === "suspended") audioCtx.resume();
      tone(880, 0, 0.25);
      tone(1175, 0.3, 0.35);
    }
    if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
  }

  function paintButton() {
    if (!soundBtn) return;
    soundBtn.textContent = wantSound && audioCtx ? "🔔 Sonido activado" : "🔔 Activar sonido";
  }

  if (soundBtn) {
    soundBtn.addEventListener("click", function () {
      unlockAudio();
      wantSound = true;
      try { localStorage.setItem("sound", "1"); } catch (e) {}
      ring();
      paintButton();
    });
  }
  // El navegador exige un toque del usuario para sonar: si ya lo había activado, se desbloquea al primer toque.
  document.addEventListener("pointerdown", function () {
    if (wantSound) { unlockAudio(); paintButton(); }
  }, { once: true });
  paintButton();

  // El chat muestra primero lo más reciente.
  function scrollChat() {
    document.querySelectorAll(".panel-chat .chat").forEach(function (c) { c.scrollTop = c.scrollHeight; });
  }
  scrollChat();
  document.body.addEventListener("htmx:afterSettle", scrollChat);

  var conn = document.getElementById("conn");
  function connect() {
    var es = new EventSource("/panel/events");
    es.onopen = function () { if (conn) conn.hidden = true; };
    es.onerror = function () { if (conn) conn.hidden = false; }; // EventSource reintenta solo
    function refresh(e) {
      var data = {};
      try { data = JSON.parse(e.data); } catch (err) {}
      htmx.trigger(document.body, "refresh", data);
    }
    es.addEventListener("new_order", function (e) { ring(); refresh(e); });
    es.addEventListener("new_message", refresh);
    es.addEventListener("status_changed", refresh);
  }
  connect();
})();
