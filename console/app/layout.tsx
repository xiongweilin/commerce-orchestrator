import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";
import NavLink from "@/components/NavLink";
import TokenInput from "@/components/TokenInput";

export const metadata: Metadata = {
  title: "运营控制台",
  description: "电商运营控制塔 - 内部运营控制台（API v1）",
  icons: [{ url: "/favicon.svg", type: "image/svg+xml" }],
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="app-header">
          <div className="brand">
            <span className="brand-mark">运</span>
            <span>运营控制台</span>
          </div>
          <nav className="nav" aria-label="主导航">
            <NavLink href="/">概览</NavLink>
            <NavLink href="/workflows">工作流</NavLink>
            <NavLink href="/sales-orders">订单</NavLink>
            <NavLink href="/return-cases">退货</NavLink>
            <NavLink href="/procurements">采购</NavLink>
            <NavLink href="/approvals">审批</NavLink>
            <NavLink href="/reconciliations">对账</NavLink>
            <NavLink href="/commands">命令</NavLink>
          </nav>
          <div className="header-right">
            <TokenInput />
          </div>
        </header>
        <main className="main">{children}</main>
        <footer className="app-footer">电商运营控制塔 · API v1</footer>
      </body>
    </html>
  );
}
