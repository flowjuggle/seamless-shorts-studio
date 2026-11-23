#!/usr/bin/env python3
"""Unit tests for Milestone M3 container & static hosting deliverables (F12, F13)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = ROOT_DIR / "tools"
DEPLOY_DIR = ROOT_DIR / "deploy"


class TestContainerHosting(unittest.TestCase):
    def test_dockerfile_structure_and_runtime(self) -> None:
        dockerfile = ROOT_DIR / "Dockerfile"
        self.assertTrue(dockerfile.is_file(), "Dockerfile is missing")
        text = dockerfile.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 0)

        self.assertRegex(text, r"(?m)^FROM\s+python:3\.11-slim-bookworm\b")
        for expected in (
            "PYTHONUNBUFFERED=1",
            "DEBIAN_FRONTEND=noninteractive",
            "HOST=0.0.0.0",
            "PORT=8766",
            "ffmpeg",
            "curl",
            "ca-certificates",
            "nodejs",
            "rm -rf /var/lib/apt/lists/*",
            "pip install --no-cache-dir -U yt-dlp",
            "WORKDIR /app",
            "seamless_shorts_agent.py",
            "START_HERE.bat",
            "START_HERE.command",
            "README.txt",
            "web/",
            "/app/web/",
            "/app/shorts_workspace",
            "python seamless_shorts_agent.py --make-demo-song",
            "EXPOSE 8766",
            "/api/health",
            "--ui --no-browser",
        ):
            self.assertIn(expected, text, f"Missing expected Dockerfile directive/content: {expected}")

        self.assertRegex(text, r"(?m)^HEALTHCHECK\b.*--interval=30s\b")

    def test_dockerignore_excludes_and_preserves_runtime(self) -> None:
        dockerignore = ROOT_DIR / ".dockerignore"
        self.assertTrue(dockerignore.is_file(), ".dockerignore is missing")
        lines = [
            line.strip()
            for line in dockerignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        for excluded in (
            ".git",
            ".agents",
            "shorts_workspace",
            "upload",
            "dist",
            "tests",
            "__pycache__",
            "*.zip",
            ".gemini",
            "flowjuggle",
            "image.png",
            ".pytest_cache",
        ):
            self.assertIn(excluded, lines, f".dockerignore should exclude {excluded}")

        for forbidden_pattern in (
            "*",
            "web",
            "web/",
            "seamless_shorts_agent.py",
            "START_HERE.bat",
            "START_HERE.command",
            "START_HERE.*",
            "README.txt",
            "*.py",
            "*.html",
            "*.js",
            "*.css",
        ):
            self.assertNotIn(
                forbidden_pattern,
                lines,
                f".dockerignore must NOT ignore runtime asset pattern: {forbidden_pattern}",
            )

    def test_docker_compose_yaml_contract(self) -> None:
        compose_path = ROOT_DIR / "docker-compose.yml"
        self.assertTrue(compose_path.is_file(), "docker-compose.yml is missing")
        data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

        services = data.get("services")
        self.assertIsInstance(services, dict)
        self.assertGreater(len(services), 0)
        svc = next(iter(services.values()))
        self.assertEqual(svc.get("build"), ".")
        self.assertIn("8766:8766", svc.get("ports", []))
        self.assertIn("shorts_data:/app/shorts_workspace", svc.get("volumes", []))

        env = svc.get("environment", {})
        self.assertEqual(env.get("HOST"), "0.0.0.0")
        self.assertEqual(str(env.get("PORT")), "8766")

        hc = svc.get("healthcheck", {})
        hc_test = hc.get("test")
        hc_joined = " ".join(hc_test) if isinstance(hc_test, list) else str(hc_test)
        self.assertIn("/api/health", hc_joined)

        top_volumes = data.get("volumes", {})
        self.assertIn("shorts_data", top_volumes)

    def test_deploy_configs_render_fly_huggingface_and_docs(self) -> None:
        # Render Blueprint YAML
        render_path = DEPLOY_DIR / "render.yaml"
        self.assertTrue(render_path.is_file())
        render_data = yaml.safe_load(render_path.read_text(encoding="utf-8"))
        self.assertIsInstance(render_data, dict)
        r_services = render_data.get("services", [])
        self.assertEqual(len(r_services), 1)
        r_svc = r_services[0]
        self.assertEqual(r_svc.get("type"), "web")
        self.assertEqual(r_svc.get("runtime"), "docker")
        self.assertEqual(r_svc.get("healthCheckPath"), "/api/health")
        env_map = {item["key"]: str(item["value"]) for item in r_svc.get("envVars", [])}
        self.assertEqual(env_map.get("HOST"), "0.0.0.0")
        self.assertEqual(env_map.get("PORT"), "8766")

        # Fly.io TOML
        fly_path = DEPLOY_DIR / "fly.toml"
        self.assertTrue(fly_path.is_file())
        fly_data = tomllib.loads(fly_path.read_text(encoding="utf-8"))
        self.assertEqual(fly_data["env"]["HOST"], "0.0.0.0")
        self.assertEqual(fly_data["env"]["PORT"], "8766")
        self.assertEqual(fly_data["http_service"]["internal_port"], 8766)
        checks = fly_data["http_service"]["checks"]
        self.assertTrue(any(c.get("path") == "/api/health" for c in checks))

        # Hugging Face Spaces README frontmatter
        hf_path = DEPLOY_DIR / "huggingface" / "README.md"
        self.assertTrue(hf_path.is_file())
        hf_text = hf_path.read_text(encoding="utf-8")
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", hf_text, re.DOTALL)
        self.assertIsNotNone(fm_match, "HF README.md must start with YAML frontmatter")
        fm = yaml.safe_load(fm_match.group(1))
        body = fm_match.group(2).strip()
        self.assertTrue(fm.get("title"))
        self.assertEqual(fm.get("sdk"), "docker")
        self.assertEqual(fm.get("app_port"), 8766)
        self.assertIs(fm.get("pinned"), False)
        self.assertGreater(len(body), 40)

        # Hosting & GitHub Pages guides
        hosting_md = (DEPLOY_DIR / "HOSTING.md").read_text(encoding="utf-8")
        for term in ("GitHub Pages", "Docker", "Hugging Face", "Fly.io", "Render", "/api/health", "8766"):
            self.assertIn(term, hosting_md)
        self.assertIsNone(
            re.search(r"http://localhost:8766", hosting_md, re.IGNORECASE),
            "deploy/HOSTING.md must not instruct users to open VM localhost:8766",
        )

        gh_pages_md = (DEPLOY_DIR / "GITHUB_PAGES.md").read_text(encoding="utf-8")
        self.assertIn("consent", gh_pages_md.lower())
        self.assertIn("tools/build_pages.sh", gh_pages_md)
        self.assertIn(".github/workflows/pages.yml", gh_pages_md)

    def test_docker_live_build_if_available(self) -> None:
        docker_bin = shutil.which("docker")
        if not docker_bin:
            self.skipTest("docker binary not installed in VM")
        res = subprocess.run([docker_bin, "--version"], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


class TestStaticPagesHosting(unittest.TestCase):
    def test_pages_workflow_yaml_contract(self) -> None:
        wf_path = ROOT_DIR / ".github" / "workflows" / "pages.yml"
        self.assertTrue(wf_path.is_file(), ".github/workflows/pages.yml is missing")
        wf_text = wf_path.read_text(encoding="utf-8")
        wf = yaml.safe_load(wf_text)
        self.assertIsInstance(wf, dict)

        perms = wf.get("permissions", {})
        self.assertEqual(perms.get("pages"), "write")
        self.assertEqual(perms.get("id-token"), "write")

        jobs = wf.get("jobs", {})
        self.assertIn("build", jobs)
        self.assertIn("deploy", jobs)

        build_steps = jobs["build"]["steps"]
        uses_list = [s.get("uses", "") for s in build_steps]
        runs_list = [s.get("run", "") for s in build_steps]
        self.assertIn("actions/checkout@v4", uses_list)
        self.assertIn("actions/setup-python@v5", uses_list)
        self.assertIn("actions/configure-pages@v5", uses_list)
        self.assertIn("actions/upload-pages-artifact@v3", uses_list)
        self.assertTrue(any("bash tools/build_pages.sh" in r for r in runs_list))

        upload_step = next(s for s in build_steps if s.get("uses") == "actions/upload-pages-artifact@v3")
        self.assertEqual(upload_step.get("with", {}).get("path"), "dist/site")

        deploy_uses = [s.get("uses", "") for s in jobs["deploy"]["steps"]]
        self.assertIn("actions/deploy-pages@v4", deploy_uses)

    def test_shellcheck_build_scripts(self) -> None:
        pages_sh = TOOLS_DIR / "build_pages.sh"
        dist_sh = TOOLS_DIR / "build_dist.sh"
        for script in (pages_sh, dist_sh):
            self.assertTrue(script.is_file(), f"Missing script: {script}")
            self.assertTrue(os.access(script, os.X_OK), f"Script must be executable: {script}")

        sc = shutil.which("shellcheck")
        if not sc:
            self.skipTest("shellcheck not installed")
        res = subprocess.run([sc, str(pages_sh), str(dist_sh)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"shellcheck failed:\n{res.stdout}\n{res.stderr}")

    def test_build_pages_creates_clean_site_bundle(self) -> None:
        pages_sh = TOOLS_DIR / "build_pages.sh"
        with tempfile.TemporaryDirectory() as tmpdir:
            site_dir = Path(tmpdir) / "site_out"
            site_dir.mkdir(parents=True, exist_ok=True)
            stale = site_dir / "stale.txt"
            stale.write_text("should be cleaned", encoding="utf-8")

            res = subprocess.run(
                ["bash", str(pages_sh), str(site_dir)],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"build_pages.sh failed:\n{res.stdout}\n{res.stderr}")
            self.assertFalse(stale.exists(), "build_pages.sh should create a clean SITE_DIR")

            expected_files = {
                "index.html",
                "styles.css",
                "app.js",
                "jszip.min.js",
                "Seamless_Shorts_Player.html",
                ".nojekyll",
            }
            actual_files = {p.name for p in site_dir.iterdir()}
            self.assertEqual(actual_files, expected_files)
            self.assertEqual((site_dir / ".nojekyll").stat().st_size, 0)

            for web_asset in (
                "index.html",
                "styles.css",
                "app.js",
                "jszip.min.js",
                "Seamless_Shorts_Player.html",
            ):
                self.assertEqual(
                    (site_dir / web_asset).read_bytes(),
                    (ROOT_DIR / "web" / web_asset).read_bytes(),
                    f"Site bundle asset differs from web/: {web_asset}",
                )

            env_site_dir = Path(tmpdir) / "site_from_env"
            env = os.environ.copy()
            env["SITE_DIR"] = str(env_site_dir)
            res_env = subprocess.run(
                ["bash", str(pages_sh)],
                cwd=str(ROOT_DIR),
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(res_env.returncode, 0, f"build_pages.sh via SITE_DIR failed:\n{res_env.stdout}\n{res_env.stderr}")
            self.assertEqual({p.name for p in env_site_dir.iterdir()}, expected_files)


if __name__ == "__main__":
    unittest.main()
