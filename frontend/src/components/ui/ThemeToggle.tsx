"use client";
import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import clsx from "clsx";

type Theme = "system" | "light" | "dark";
const KEY = "ayvid-theme";

function applyTheme(t: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = t === "dark" || (t === "system" && prefersDark);
  root.classList.toggle("dark", isDark);
  root.dataset.theme = isDark ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const saved = (localStorage.getItem(KEY) as Theme | null) ?? "system";
    setTheme(saved);
    applyTheme(saved);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const cur = (localStorage.getItem(KEY) as Theme | null) ?? "system";
      if (cur === "system") applyTheme("system");
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const set = (t: Theme) => {
    setTheme(t);
    localStorage.setItem(KEY, t);
    applyTheme(t);
  };

  const Btn = ({ value, icon, label }: { value: Theme; icon: React.ReactNode; label: string }) => (
    <button
      type="button"
      onClick={() => set(value)}
      title={label}
      className={clsx(
        "grid h-7 w-7 place-items-center rounded-full transition-colors",
        theme === value
          ? "bg-[hsl(var(--bg-sunken))] text-fg"
          : "text-muted hover:text-fg hover:bg-[hsl(var(--bg-sunken))]",
      )}
    >
      {icon}
    </button>
  );

  return (
    <div className="flex items-center gap-0.5 rounded-full border border-app bg-panel p-0.5">
      <Btn value="light" icon={<Sun className="h-4 w-4" />} label="Light" />
      <Btn value="system" icon={<Monitor className="h-4 w-4" />} label="System" />
      <Btn value="dark" icon={<Moon className="h-4 w-4" />} label="Dark" />
    </div>
  );
}
