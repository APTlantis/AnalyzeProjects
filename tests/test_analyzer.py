import os
import pathlib
import tempfile
import unittest
from unittest import mock

import requests

import ModelClient
import Summarizer
from PromptTemplate import PROJECT_TEMPLATE, build_project_prompt


class ProjectIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = Summarizer.load_config("config.example.toml")

    def test_real_index_has_exact_inventory(self):
        targets = Summarizer.discover_projects(self.config)
        counts = {group: 0 for group in Summarizer.EXPECTED_GROUP_COUNTS}
        for target in targets:
            counts[target.project_group] += 1
        self.assertEqual(33, len(targets))
        self.assertEqual(Summarizer.EXPECTED_GROUP_COUNTS, counts)
        self.assertIn("QB-Winget", {target.project_name for target in targets})
        self.assertIn("WingettingQB64", {target.project_name for target in targets})
        self.assertIn("ConversionTools", {target.project_name for target in targets})
        normalized_paths = {Summarizer.normalize_path(target.project_path) for target in targets}
        self.assertIn("d:/cts/holyc-llama", normalized_paths)
        self.assertNotIn("d:/cts/llama", normalized_paths)
        self.assertNotIn("Tauri-IT", {target.project_name for target in targets})
        self.assertNotIn("LinuxGenealogy", {target.project_name for target in targets})
        standards = [target for target in targets if target.project_group == "STANDARDS"]
        self.assertTrue(all(".library\\aptlantis_core" in str(target.project_path) for target in standards))

    def test_manifest_is_optional_and_folder_named_manifest_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = pathlib.Path(temp_dir) / "Example"
            project.mkdir()
            (project / "project-manifest.toml").write_text("[project]\nname='other'", encoding="utf-8")
            preferred = project / "Example.manifest.toml"
            preferred.write_text("[project]\nname='Example'", encoding="utf-8")
            self.assertEqual(preferred.resolve(), Summarizer.select_canonical_manifest(project))
            preferred.unlink()
            (project / "second.manifest.toml").write_text("[project]\nname='second'", encoding="utf-8")
            self.assertIsNone(Summarizer.select_canonical_manifest(project))

    def test_invalid_index_reports_missing_duplicate_and_counts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            existing = root / "Existing"
            existing.mkdir()
            index = root / "ProjectIndex.md"
            index.write_text(
                f"## 1. DRS\n- {existing}\n- {existing}\n- {root / 'Missing'}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate project path"):
                Summarizer.parse_project_index(index, self.config["standards"])


class PromptAndNormalizationTests(unittest.TestCase):
    def test_application_prompt_contains_only_selected_standard(self):
        prompt = build_project_prompt("Tool", "content", "CTS", "CTS UNIQUE TEXT", "cts.md")
        self.assertIn("CTS UNIQUE TEXT", prompt)
        self.assertNotIn("EVALUATION MODE: GOVERNANCE STANDARD", prompt)

    def test_standard_prompt_uses_internal_quality_rubric(self):
        prompt = build_project_prompt("CTS", "content", "STANDARDS")
        self.assertIn("EVALUATION MODE: GOVERNANCE STANDARD", prompt)
        self.assertIn("clarity, completeness, consistency, implementability", prompt)

    def test_transfer_artifact_claim_is_not_actionable(self):
        response = {key: ([] if isinstance(value, list) else value) for key, value in PROJECT_TEMPLATE.items()}
        response["missing_pieces"] = ["Source appears truncated by the context window", "Document the exit codes"]
        Summarizer.normalize_transfer_artifact_claims(response)
        self.assertEqual(["Document the exit codes"], response["missing_pieces"])
        self.assertTrue(any("suppressed" in note for note in response["notes"]))


class ModelClientTests(unittest.TestCase):
    def test_openai_success_and_json_extraction(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": '{"status":"ok"}'}}]}
        with mock.patch("ModelClient.requests.post", return_value=response) as post:
            result = ModelClient.ask_model_json("gpt-5-mini-2025-08-07", "prompt", "https://api.openai.com", "key", "openai")
        self.assertEqual({"status": "ok"}, result)
        self.assertEqual("Bearer key", post.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual({"type": "json_object"}, post.call_args.kwargs["json"]["response_format"])
        self.assertNotIn("temperature", post.call_args.kwargs["json"])

    def test_malformed_json_raises(self):
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": "not json"}}]}
        with mock.patch("ModelClient.requests.post", return_value=response):
            with self.assertRaises(ValueError):
                ModelClient.ask_model_json("model", "prompt", "https://api.openai.com", "key", "openai")

    def test_authentication_error_propagates(self):
        response = mock.Mock()
        response.raise_for_status.side_effect = requests.HTTPError("401 unauthorized")
        with mock.patch("ModelClient.requests.post", return_value=response):
            with self.assertRaisesRegex(requests.HTTPError, "401"):
                ModelClient.ask_model_json("model", "prompt", "https://api.openai.com", "bad-key", "openai")

    def test_retry_covers_rate_limit(self):
        failure = requests.HTTPError("429 rate limit")
        operation = mock.Mock(side_effect=[failure, {"ok": True}])
        with mock.patch("Summarizer.time.sleep"):
            result = Summarizer.retry_with_backoff(operation, 2)
        self.assertEqual({"ok": True}, result)
        self.assertEqual(2, operation.call_count)

    def test_environment_key_is_required(self):
        config = {"model": {"prefer": "openai"}}
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                Summarizer.validate_runtime_config(config)


if __name__ == "__main__":
    unittest.main()
