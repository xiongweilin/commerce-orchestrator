import Link from "next/link";

export default function NotFound() {
  return (
    <div className="page">
      <h1>未找到</h1>
      <p className="page-sub">请求的资源不存在，或后端返回了 404。</p>
      <div>
        <Link className="btn btn-secondary" href="/">
          返回概览
        </Link>
      </div>
    </div>
  );
}
