"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/** 客户端刷新按钮：触发 router.refresh() 让服务端组件重新拉取数据。 */
export default function RefreshButton({
  label = "刷新",
  className = "",
}: {
  label?: string;
  className?: string;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const handleClick = () => {
    setLoading(true);
    const result = router.refresh() as unknown as Promise<void> | void;
    Promise.resolve(result).finally(() => setLoading(false));
  };

  return (
    <button
      type="button"
      className={`btn btn-secondary ${className}`}
      onClick={handleClick}
      disabled={loading}
    >
      {loading ? "刷新中…" : `↻ ${label}`}
    </button>
  );
}
