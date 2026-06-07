/* vs-track.js — Verum Signal page view tracker
   No cookies, no third-party, no PII. Session = random ID in sessionStorage.
   Sends: path, referrer, screen width, duration on leave. */
(function(){
  if (typeof navigator !== 'undefined' && navigator.userAgent && /bot|crawl|spider|slurp/i.test(navigator.userAgent)) return;

  var sid = sessionStorage.getItem('vs_sid');
  if (!sid) {
    sid = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    sessionStorage.setItem('vs_sid', sid);
  }

  var start = Date.now();
  var pvId = null;
  var sent = false;

  // Record page view
  try {
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/pv', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.onload = function() {
      try { pvId = JSON.parse(xhr.responseText).id; } catch(e) {}
    };
    xhr.send(JSON.stringify({
      p: location.pathname + location.search,
      r: document.referrer || null,
      s: sid,
      w: window.innerWidth
    }));
  } catch(e) {}

  // Send duration on page leave
  function sendDuration() {
    if (sent || !pvId) return;
    sent = true;
    var ms = Date.now() - start;
    if (ms < 500) return; // skip bounces under 500ms
    var data = JSON.stringify({ id: pvId, d: ms });
    if (navigator.sendBeacon) {
      navigator.sendBeacon('/api/pv/duration', data);
    } else {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/pv/duration', false);
      xhr.setRequestHeader('Content-Type', 'application/json');
      xhr.send(data);
    }
  }

  document.addEventListener('visibilitychange', function() {
    if (document.visibilityState === 'hidden') sendDuration();
  });
  window.addEventListener('beforeunload', sendDuration);
  window.addEventListener('pagehide', sendDuration);
})();
