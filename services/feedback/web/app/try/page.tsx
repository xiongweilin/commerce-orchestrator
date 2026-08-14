import { PageHead } from "@/components/UI";
import { TryForm } from "./try-form";

export default function TryPage() {
  return <><PageHead eyebrow="LIVE SANDBOX" title="提交一条虚构工单" description="请勿输入真实个人信息。每个匿名会话每天最多分析 5 条，结果 24 小时后清理。" /><TryForm /></>;
}

