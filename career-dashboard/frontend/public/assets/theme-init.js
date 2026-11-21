// Applies the person's chosen theme before the first paint, so a dark-mode choice never
// flashes white. A separate file because the app's Content-Security-Policy forbids inline
// scripts. "system" (or nothing saved) leaves the choice to the computer's setting.
(function () {
  try {
    var choice = window.localStorage.getItem("career-theme");
    if (choice === "light" || choice === "dark") {
      document.documentElement.setAttribute("data-theme", choice);
    }
  } catch (error) {
    // Storage can be blocked (private window, site data off): the system theme applies.
  }
})();
