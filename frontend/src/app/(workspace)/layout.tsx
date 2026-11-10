import "../workspace.css";
import type { ReactNode } from "react";
import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
