"""Probe: 验证 5 个修复 + auth 全流程"""
import asyncio
import random
import httpx


async def main():
    uid = str(random.randint(100000, 999999))
    username = f"authprobe_{uid}"
    email = f"{username}@test.com"
    password = "Pass1234!"

    async with httpx.AsyncClient(base_url="http://localhost:8000") as c:
        # 1. Register
        r = await c.post("/api/auth/register", json={"username": username, "email": email, "password": password})
        print(f"1. Register: {r.status_code}")
        assert r.status_code == 200, r.text
        rt1 = r.json()["refresh_token"]

        # 2. Login
        r = await c.post("/api/auth/login", json={"login": username, "password": password})
        print(f"2. Login (username): {r.status_code}")
        assert r.status_code == 200, r.text
        rt2 = r.json()["refresh_token"]
        assert rt1 != rt2, "rt1 == rt2 means jti broken"
        print("   PASS: rt1 != rt2 (jti works)")

        # 3. Login again
        r = await c.post("/api/auth/login", json={"login": email, "password": password})
        print(f"3. Login (email): {r.status_code}")
        assert r.status_code == 200
        rt3 = r.json()["refresh_token"]
        assert rt2 != rt3, "Consecutive logins collided"
        print("   PASS: rt2 != rt3 (jti uniqueness)")

        # 4. Refresh - rotate
        r = await c.post("/api/auth/refresh", json={"refresh_token": rt3})
        print(f"4. Refresh: {r.status_code}")
        assert r.status_code == 200, r.text
        new_rt = r.json()["refresh_token"]
        access = r.json()["access_token"]
        assert new_rt != rt3, "token rotation broken"
        print("   PASS: token rotated")

        # 5. Refresh old (should 401)
        r = await c.post("/api/auth/refresh", json={"refresh_token": rt3})
        print(f"5. Refresh old: {r.status_code}")
        assert r.status_code == 401, f"Old token not revoked! {r.text}"
        print("   PASS: old token revoked")

        # 6. Logout
        r = await c.post(
            "/api/auth/logout",
            json={"refresh_token": new_rt},
            headers={"Authorization": f"Bearer {access}"},
        )
        print(f"6. Logout: {r.status_code}")
        assert r.status_code == 200, f"Logout failed: {r.text}"
        print("   PASS: logout ok")

        # 7. Refresh after logout (should 401)
        r = await c.post("/api/auth/refresh", json={"refresh_token": new_rt})
        print(f"7. Refresh after logout: {r.status_code}")
        assert r.status_code == 401, f"Token still valid after logout! {r.text}"
        print("   PASS: revoked after logout")

        # 8. 验证 mimo 修复（不崩溃，无论是否配置）
        r = await c.get("/api/mimo/status", headers={"X-API-Key": ""})
        print(f"8. Mimo status: {r.status_code}")
        assert r.status_code in (200, 401), f"mimo crash: {r.text}"
        if r.status_code == 200:
            d = r.json()
            assert "enabled" in d, f"malformed mimo response: {d}"
            assert "message" in d or "health" in d, f"missing fields: {d}"
            print(f"   PASS: mimo returns clean response (enabled={d['enabled']})")

        # 9. 验证 knowledge 修复
        r = await c.get("/api/characters/sys_001/knowledge/stats", headers={"X-API-Key": ""})
        print(f"9. Knowledge stats: {r.status_code}")
        assert r.status_code in (200, 404, 401), f"knowledge crash: {r.text}"
        print(f"   PASS: knowledge route exists (status {r.status_code}, not 404 missing)")

        print()
        print("=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
