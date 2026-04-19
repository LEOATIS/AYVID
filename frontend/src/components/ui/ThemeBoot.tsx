/**
 * Inline script injected pre-paint to set the correct theme class
 * before the first render — eliminates flash-of-wrong-theme.
 */
export function ThemeBootScript() {
  const code = `(function(){try{var k=localStorage.getItem('ayvid-theme')||'system';var d=k==='dark'||(k==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);var r=document.documentElement;r.classList.toggle('dark',d);r.dataset.theme=d?'dark':'light';}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: code }} />;
}
