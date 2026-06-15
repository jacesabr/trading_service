"""test_redaction.py — the public dashboard must never leak strategy internals.

Run: python test_redaction.py   (no pytest needed)

Guards the product rule: results + the verifiable track record are public; HOW an
edge works or was found (method/risk/params/rules/gate/provenance) is admin-only.
"""
import base64
import os

os.environ.setdefault("ADMIN_PASSWORD", "test-pw")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("TRADE_DB", "tracker_test.db")

import dashboard_db as D

PRIVATE = {"method", "risk", "signal", "gate", "provenance", "exec_model",
           "experiments", "versions"}


def main():
    app = D.app.test_client()

    pub = app.get("/api/stats").get_json()
    assert pub["strategies"], "no strategies in public payload"
    for name, card in pub["strategies"].items():
        leaked = PRIVATE & set(card)
        assert not leaked, f"PUBLIC leaked private fields for {name}: {leaked}"

    assert app.get("/api/admin/stats").status_code == 401, "admin not gated"

    bad = base64.b64encode(b"admin:nope").decode()
    assert app.get("/api/admin/stats",
                   headers={"Authorization": "Basic " + bad}).status_code == 401

    tok = base64.b64encode(
        f"admin:{os.environ['ADMIN_PASSWORD']}".encode()).decode()
    r = app.get("/api/admin/stats", headers={"Authorization": "Basic " + tok})
    assert r.status_code == 200, f"admin auth failed: {r.status_code}"
    adm = r.get_json()
    sample = next(iter(adm["strategies"].values()))
    assert "method" in sample and "signal" in sample, "admin missing internals"

    print("OK — public redacted; admin gated (401 no-auth / 401 bad-pw / 200 ok); "
          f"{len(pub['strategies'])} strategies checked.")


if __name__ == "__main__":
    main()
