import { useEffect } from "react";
import { useLoad } from "./ui";
import type { Json } from "./api";

export function ProviderUsage({ providerId }: { providerId: string }) {
  const { data, reload, error } = useLoad<Json>(`/providers/${providerId}/usage`, {});
  useEffect(() => { const timer = setInterval(() => void reload(), 10000); return () => clearInterval(timer); }, [reload]);
  if (error) return <small role="status">Usage unavailable</small>;
  if (data.limit_usd === undefined) return null;
  return <div className="provider-usage" style={{ gridColumn: "1 / -1", fontSize: 12 }}>
    <span>API estimate: ${Number(data.estimated_cost_usd || 0).toFixed(4)}</span>
    {data.limit_usd !== null && <span> · Limit: ${Number(data.limit_usd).toFixed(2)} · Available: ${Number(data.remaining_usd || 0).toFixed(4)}</span>}
    {data.reserved_usd > 0 && <span> · Reserved: ${Number(data.reserved_usd).toFixed(4)}</span>}
    {data.uncertain_requests > 0 && <span> · {data.uncertain_requests} request(s) awaiting usage confirmation</span>}
  </div>;
}
