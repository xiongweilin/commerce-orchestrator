import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  ["/", "总览"],
  ["/tickets", "工单池"],
  ["/clusters", "问题簇"],
  ["/sop", "候选 SOP"],
  ["/report", "周报"],
  ["/try", "在线体验"],
  ["/evaluation", "评测"],
  ["/about", "边界"],
];

const portfolioLinks = [
  ["/index", "作品首页"],
  ["/catalog-ops", "商品自动化"],
];

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">F/</span>
          <div><strong>反馈结构化</strong><small>OPERATIONS AGENT</small></div>
        </div>
        <nav>
          {links.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}
          {portfolioLinks.map(([href, label]) => <a key={href} href={href}>{label}</a>)}
        </nav>
        <div className="sidebar-note"><span className="status-dot" /> 合成数据演示环境</div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
