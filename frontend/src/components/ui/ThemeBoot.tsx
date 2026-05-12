/**
 * Inline script injected pre-paint to set the correct theme class
 * before the first render — eliminates flash-of-wrong-theme.
 *
 * Default behaviour:
 *   - If the user has explicitly chosen 'light' / 'dark' / 'system' before,
 *     honour their stored preference verbatim.
 *   - If there's no stored preference AND we're on a marketing route
 *     (anything that isn't /console/*), force dark mode.  Marketing is
 *     designed to be experienced in the warm-graphite register first; a
 *     visitor coming from a system that happens to be in light mode
 *     should still see the dark hero on first impression.  They can flip
 *     to light via the theme toggle and that choice is then persisted.
 *   - On /console/* with no stored preference, fall through to OS
 *     preference (system) so operators using a light IDE keep their flow.
 */
export function ThemeBootScript() {
  // Marketing routes ALWAYS render dark — stored preference is ignored
  // there.  Console routes respect the visitor's stored or system pref.
  // The decision tree: on /console/* use stored||'system'; everywhere
  // else just force 'dark'.  The toggle is hidden on the marketing nav,
  // so a marketing visitor can't get into a state where their click
  // appears to do nothing.
  const code = `(function(){try{var stored=localStorage.getItem('ayvid-theme');var path=location.pathname||'/';var onConsole=path.indexOf('/console')===0;var effective=onConsole?(stored||'system'):'dark';var prefersDark=matchMedia('(prefers-color-scheme: dark)').matches;var isDark=effective==='dark'||(effective==='system'&&prefersDark);var r=document.documentElement;r.classList.toggle('dark',isDark);r.dataset.theme=isDark?'dark':'light';}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
