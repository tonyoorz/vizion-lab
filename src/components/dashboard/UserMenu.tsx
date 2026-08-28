import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { refreshAuth, useAuth } from "@/hooks/useAuth";
import { logout } from "@/lib/auth";

/** 头部用户身份 + 登出按钮。 */
const UserMenu = () => {
  const navigate = useNavigate();
  const { username } = useAuth();
  const [busy, setBusy] = useState(false);

  const handleLogout = async () => {
    if (busy) return;
    setBusy(true);
    await logout();
    await refreshAuth();
    setBusy(false);
    toast.success("已登出");
    navigate("/login", { replace: true });
  };

  if (!username) {
    return null;
  }

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span className="font-medium text-foreground">{username}</span>
      <Button variant="ghost" size="sm" onClick={handleLogout} disabled={busy} className="h-7 px-2 text-xs">
        {busy ? "…" : "登出"}
      </Button>
    </div>
  );
};

export default UserMenu;
