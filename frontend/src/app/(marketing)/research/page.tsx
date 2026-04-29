import Link from "next/link";

export const metadata = {
  title: "Research",
  description:
    "Selected research notes from AYVID — on causal supply-chain inference, agent-driven debate, and event-conditioned trading.",
};

const NOTES: { date: string; tag: string; title: string; teaser: string }[] = [
  {
    date: "2026 · Q1",
    tag: "Method",
    title: "Why naive walk-forward backtests are an indictment of the trader, not the market.",
    teaser:
      "Trading every ticker every day on technicals lost 99.98% over an 8-year window in our shop. The same engine, restricted to event-conditioned windows surfaced by the graph, returned +33.43% compounded across 20 named shocks with a 63.7% win rate. The full report is in the operations console.",
  },
  {
    date: "2026 · Q2",
    tag: "Architecture",
    title: "A four-agent swarm produces calibrated conviction. A single model does not.",
    teaser:
      "Strategist, Mapper, Auditor, Supervisor. We measure their disagreement as a first-class signal — high-disagreement trades are systematically smaller, regardless of average conviction.",
  },
  {
    date: "2026 · Q2",
    tag: "Data",
    title: "What an N-th order supply-chain graph actually buys you.",
    teaser:
      "On the Red Sea closure of late 2024, the graph proposed bunker-fuel beneficiaries (BP) two hops from the shock — names a keyword search would miss. The relevant traversal is exhibit 1 in the AYVID 10-K corpus.",
  },
  {
    date: "2026 · Q2",
    tag: "Risk",
    title: "Independent kill switches are not paranoia. They are uptime.",
    teaser:
      "An out-of-process C++ watchdog flattened the book three times in shadow-mode last quarter. Twice it was wrong. Once it wasn't. We will keep it.",
  },
];

export default function ResearchPage() {
  return (
    <>
      <section className="border-b border-app">
        <div className="container-page section-y reveal">
          <p className="eyebrow">Research</p>
          <h1 className="font-display display-1 mt-6 max-w-[18ch] text-fg">
            Notes from the desk.
          </h1>
          <p className="lede mt-7">
            We publish selected research externally — methodology, philosophy, and
            counterintuitive findings — and keep proprietary signal work inside the
            firm. Below is what we&rsquo;re comfortable saying out loud.
          </p>
        </div>
      </section>

      <section>
        <div className="container-page section-y">
          <ul className="space-y-px overflow-hidden border border-app bg-[hsl(var(--border))]">
            {NOTES.map((n, i) => (
              <li
                key={n.title}
                className="group bg-panel p-7 transition-colors hover:bg-[hsl(var(--bg-sunken))]"
              >
                <div className="grid grid-cols-1 gap-6 md:grid-cols-12">
                  <div className="md:col-span-3">
                    <div className="num text-[12px] uppercase tracking-[0.16em] text-subtle">
                      {n.date}
                    </div>
                    <div className="mt-1 inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.18em] text-brand">
                      <span aria-hidden>—</span>
                      {n.tag}
                    </div>
                  </div>
                  <div className="md:col-span-9">
                    <h2 className="font-display text-[24px] leading-snug text-fg group-hover:text-brand transition-colors">
                      {n.title}
                    </h2>
                    <p className="prose-body mt-3 max-w-prose">{n.teaser}</p>
                    <span className="mt-4 inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.16em] text-subtle">
                      Note {String(i + 1).padStart(2, "0")} · Internal release
                    </span>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="border-t border-app bg-[hsl(var(--bg-elev))]">
        <div className="container-page section-y-sm flex flex-col items-start justify-between gap-6 md:flex-row md:items-center">
          <p className="prose-body max-w-2xl">
            Verifying any of the above against the live system requires console
            access. LP and YC partner enquiries get a passphrase the same day.
          </p>
          <Link href="/contact" className="btn-primary">
            Contact the desk →
          </Link>
        </div>
      </section>
    </>
  );
}
