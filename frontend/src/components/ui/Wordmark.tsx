/** AYVID horizontal wordmark that follows the active theme (html.dark). */
export function Wordmark({ height, alt = "AYVID" }: { height: number; alt?: string }) {
  const style = { height, width: "auto" } as const;
  return (
    <>
      <img src="/brand/wordmark-horizontal-light.svg" alt={alt} className="wordmark-on-light" style={style} />
      <img src="/brand/wordmark-horizontal-dark.svg" alt="" aria-hidden className="wordmark-on-dark" style={style} />
    </>
  );
}
