/* QU ACM link shortener - copy buttons (loaded as an external file so
   the Content-Security-Policy with script-src 'self' stays happy). */
document.addEventListener("click", function (event) {
  var btn = event.target.closest(".copy-btn");
  if (!btn) return;

  var text = btn.getAttribute("data-copy") || "";
  var finish = function () {
    var original = btn.textContent;
    btn.textContent = "Copied!";
    btn.disabled = true;
    setTimeout(function () {
      btn.textContent = original;
      btn.disabled = false;
    }, 1500);
  };

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(finish);
  } else {
    // Fallback for older browsers.
    var area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
    finish();
  }
});
