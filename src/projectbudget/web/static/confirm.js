/* Confirmation for destructive plain forms (issue #17).
 * A form with data-confirm="message" asks before submitting. "{target}" in the message is replaced
 * with the label of the option chosen in the form's first <select>, so a merge names both items.
 * htmx-driven buttons use hx-confirm instead; this covers ordinary POST forms. No inline script
 * (CSP script-src 'self'). */
(function () {
  "use strict";

  function message(form) {
    var text = form.getAttribute("data-confirm") || "";
    var select = form.querySelector("select");
    if (select && text.indexOf("{target}") !== -1) {
      var option = select.options[select.selectedIndex];
      var label = option && option.value ? option.textContent.trim() : "";
      text = text.replace("{target}", label);
    }
    return text;
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-confirm")) {
      return;
    }
    if (!window.confirm(message(form))) {
      event.preventDefault();
    }
  });
})();
