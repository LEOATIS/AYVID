import { Suspense } from "react";
import { ResearchView } from "@/components/workspace/ResearchView";

export const metadata = { title: "Research" };

export default function ResearchPage() {
  return (
    <Suspense fallback={<p className="ws-empty">Loading…</p>}>
      <ResearchView />
    </Suspense>
  );
}
