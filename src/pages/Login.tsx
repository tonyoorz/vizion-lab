import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { refreshAuth, useAuth } from "@/hooks/useAuth";
import { login } from "@/lib/auth";

const Login = () => {
  const navigate = useNavigate();
  const { authenticated, loading } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 已登录则跳回首页
  if (authenticated) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    const result = await login(username, password);
    setSubmitting(false);
    if (result.ok) {
      toast.success("登录成功");
      // 刷新 auth store，让 RequireAuth 认到已登录，再跳转
      await refreshAuth();
      navigate("/", { replace: true });
    } else {
      toast.error(result.error ?? "登录失败");
    }
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-muted">
        <p className="text-muted-foreground">加载中…</p>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-2xl">Vizion Lab 登录</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">BMW 账号 (qxxxxxx)</Label>
              <Input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="q123456"
                autoComplete="username"
                required
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                disabled={submitting}
              />
            </div>
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "登录中…" : "登录"}
            </Button>
          </form>
          <p className="mt-4 text-center text-xs text-muted-foreground">
            使用 BMW 内网 SSO 凭证登录，将以本人身份访问 Octane
          </p>
        </CardContent>
      </Card>
    </div>
  );
};

export default Login;
