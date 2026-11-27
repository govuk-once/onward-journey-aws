import base64
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, NoCredentialsError
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rotate_tester_passwords as rot


class TestParseTargets:
    def test_valid_and_deduplicates_preserving_order(self):
        assert rot.parse_targets(["tester-4", "tester-1", "tester-4"]) == ["4", "1"]

    @pytest.mark.parametrize("bad", ["tester-0", "tester_1", "1", "tester-1a", ""])
    def test_rejects_invalid_formats(self, bad):
        with pytest.raises(ValueError, match="Invalid target"):
            rot.parse_targets([bad])

    def test_empty_list(self):
        assert rot.parse_targets([]) == []


class TestEmptyFiles:
    def test_empty_json_array_file_exits_cleanly_without_terraform(self, tmp_path):
        f = tmp_path / "empty.json"
        f.write_text("[]", encoding="utf-8")
        with patch("rotate_tester_passwords.check_for_drift") as mock_drift:
            assert rot.main(["--targets-file", str(f)]) == 0
            mock_drift.assert_not_called()

    def test_blank_file_returns_error(self, tmp_path, capsys):
        f = tmp_path / "blank.json"
        f.write_text("", encoding="utf-8")
        assert rot.main(["--targets-file", str(f)]) == 1
        stderr = capsys.readouterr().err
        assert "Error:" in stderr
        assert "Expecting value" in stderr


class TestMultipleTargets:
    @patch("rotate_tester_passwords.subprocess.run")
    def test_rotate_in_terraform_passes_multiple_replace_flags(self, mock_run, tmp_path):
        rot.rotate_in_terraform(tmp_path, ["1", "4"])
        mock_run.assert_called_once_with(
            [
                "terraform",
                f"-chdir={tmp_path}",
                "apply",
                '-replace=random_password.tester_passwords["1"]',
                '-replace=random_password.tester_passwords["4"]',
            ],
            check=True,
        )

    def test_sync_updates_multiple_branches_in_one_run(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "branches": [
                    {"branchName": "tester-1-aaa"},
                    {"branchName": "tester-2-bbb"},
                    {"branchName": "tester-4-ccc"},
                ]
            }
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {
                    "Tester 1": {"username": "u1", "password": "p1"},
                    "Tester 4": {"username": "u4", "password": "p4"},
                }
            )
        }

        rot.sync_branch_credentials(
            amplify_client=mock_amplify,
            secrets_client=mock_secrets,
            app_id="app-123",
            secret_id="dev/frontend/tester-credentials",
            indices=["1", "4"],
        )

        assert mock_amplify.update_branch.call_count == 2
        updated_branches = [
            call.kwargs["branchName"]
            for call in mock_amplify.update_branch.call_args_list
        ]
        assert updated_branches == ["tester-1-aaa", "tester-4-ccc"]

    @patch("rotate_tester_passwords.sync_branch_credentials")
    @patch("rotate_tester_passwords.rotate_in_terraform")
    @patch("rotate_tester_passwords.check_for_drift")
    @patch(
        "rotate_tester_passwords.list_all_branches",
        return_value=["tester-1-aaa", "tester-4-ccc"],
    )
    @patch("rotate_tester_passwords.boto3.client")
    @patch(
        "rotate_tester_passwords.get_terraform_output",
        side_effect=["app-123", "sec-123"],
    )
    def test_prints_deduplicated_target_summary_before_apply(
        self,
        mock_output,
        mock_boto,
        mock_list,
        mock_drift,
        mock_rotate,
        mock_sync,
        capsys,
    ):
        # Simulate user cancelling at the Terraform prompt; if the summary is in
        # stdout after main() aborts here, it must have printed before apply ran.
        mock_rotate.side_effect = subprocess.CalledProcessError(1, "terraform")

        assert rot.main(["tester-4", "tester-1", "tester-4"]) == 1
        captured = capsys.readouterr()
        assert "Targets to rotate: tester-4, tester-1" in captured.out
        mock_rotate.assert_called_once_with(rot.SERVICES_DIR, ["4", "1"])
        mock_sync.assert_not_called()


class TestLoadTargetsFromFile:
    def test_loads_valid_json_array(self, tmp_path):
        f = tmp_path / "targets.json"
        f.write_text(json.dumps(["tester-1", "tester-2"]), encoding="utf-8")
        assert rot.load_targets_from_file(f) == ["1", "2"]

    def test_rejects_non_list_json(self, tmp_path):
        f = tmp_path / "targets.json"
        f.write_text(json.dumps({"target": "tester-1"}), encoding="utf-8")
        with pytest.raises(ValueError, match="JSON array"):
            rot.load_targets_from_file(f)

    def test_rejects_malformed_json(self, tmp_path):
        f = tmp_path / "targets.json"
        f.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            rot.load_targets_from_file(f)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rot.load_targets_from_file(tmp_path / "missing.json")


class TestBranchMatchingAndPagination:
    def test_finds_unique_branch(self):
        branches = ["tester-1-abc123", "tester-10-def456", "tester-2-ghi789"]
        assert rot.find_branch_name(branches, "1") == "tester-1-abc123"
        assert rot.find_branch_name(branches, "10") == "tester-10-def456"

    def test_raises_when_branch_missing(self):
        with pytest.raises(LookupError, match="Could not find Amplify branch"):
            rot.find_branch_name(["tester-1-abc123"], "2")

    def test_raises_on_ambiguous_matches(self):
        with pytest.raises(LookupError, match="Multiple Amplify branches"):
            rot.find_branch_name(["tester-1-aaa", "tester-1-bbb"], "1")

    def test_list_all_branches_paginates(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"branches": [{"branchName": "tester-1-aaa"}]},
            {"branches": [{"branchName": "tester-2-bbb"}]},
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        result = rot.list_all_branches(mock_amplify, "app-123")
        assert result == ["tester-1-aaa", "tester-2-bbb"]
        mock_amplify.get_paginator.assert_called_once_with("list_branches")


class TestDriftGuard:
    @patch("rotate_tester_passwords.subprocess.run")
    def test_passes_when_no_changes(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=0)
        rot.check_for_drift(tmp_path)

    @patch("rotate_tester_passwords.subprocess.run")
    def test_aborts_on_pending_drift(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=2, stderr="")
        with pytest.raises(RuntimeError, match="Unrelated pending Terraform changes"):
            rot.check_for_drift(tmp_path)

    @patch("rotate_tester_passwords.subprocess.run")
    def test_aborts_on_plan_error(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth error")
        with pytest.raises(RuntimeError, match="Pre-flight terraform plan failed"):
            rot.check_for_drift(tmp_path)


class TestSyncBranchCredentials:
    def test_updates_targeted_branches_in_memory(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"branches": [{"branchName": "tester-1-xyz"}, {"branchName": "tester-2-uvw"}]}
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {"Tester 1": {"username": "tester-1", "password": "secret-pw"}}
            )
        }

        rot.sync_branch_credentials(
            amplify_client=mock_amplify,
            secrets_client=mock_secrets,
            app_id="app-123",
            secret_id="dev/frontend/tester-credentials",
            indices=["1"],
        )

        expected_b64 = base64.b64encode(b"tester-1:secret-pw").decode("utf-8")
        mock_amplify.update_branch.assert_called_once_with(
            appId="app-123",
            branchName="tester-1-xyz",
            enableBasicAuth=True,
            basicAuthCredentials=expected_b64,
        )

    def test_raises_if_secret_entry_missing(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"branches": [{"branchName": "tester-1-xyz"}]}
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {"SecretString": json.dumps({})}

        with pytest.raises(LookupError, match="Tester 1"):
            rot.sync_branch_credentials(
                amplify_client=mock_amplify,
                secrets_client=mock_secrets,
                app_id="app-123",
                secret_id="dev/frontend/tester-credentials",
                indices=["1"],
            )


class TestRecoveryAndPreValidation:
    def test_sync_does_not_update_any_branch_if_one_target_is_missing(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"branches": [{"branchName": "tester-1-aaa"}]}
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {"Tester 1": {"username": "u1", "password": "p1"}}
            )
        }

        with pytest.raises(LookupError, match="tester-99"):
            rot.sync_branch_credentials(
                amplify_client=mock_amplify,
                secrets_client=mock_secrets,
                app_id="app-123",
                secret_id="dev/frontend/tester-credentials",
                indices=["1", "99"],
            )

        mock_amplify.update_branch.assert_not_called()

    def test_sync_does_not_update_any_branch_if_one_secret_is_missing(self):
        mock_amplify = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "branches": [
                    {"branchName": "tester-1-aaa"},
                    {"branchName": "tester-2-bbb"},
                ]
            }
        ]
        mock_amplify.get_paginator.return_value = mock_paginator

        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {"Tester 1": {"username": "u1", "password": "p1"}}
            )
        }

        with pytest.raises(LookupError, match="Tester 2"):
            rot.sync_branch_credentials(
                amplify_client=mock_amplify,
                secrets_client=mock_secrets,
                app_id="app-123",
                secret_id="dev/frontend/tester-credentials",
                indices=["1", "2"],
            )

        mock_amplify.update_branch.assert_not_called()

    @patch("rotate_tester_passwords.sync_branch_credentials")
    @patch("rotate_tester_passwords.rotate_in_terraform")
    @patch("rotate_tester_passwords.check_for_drift")
    @patch("rotate_tester_passwords.list_all_branches", return_value=["tester-1-aaa"])
    @patch("rotate_tester_passwords.boto3.client")
    @patch(
        "rotate_tester_passwords.get_terraform_output",
        side_effect=["app-123", "sec-123"],
    )
    def test_main_aborts_before_terraform_if_target_branch_is_missing(
        self,
        mock_output,
        mock_boto,
        mock_list,
        mock_drift,
        mock_rotate,
        mock_sync,
        capsys,
    ):
        assert rot.main(["tester-1", "tester-99"]) == 1
        mock_drift.assert_not_called()
        mock_rotate.assert_not_called()
        mock_sync.assert_not_called()
        assert "tester-99" in capsys.readouterr().err

    @patch("rotate_tester_passwords.sync_branch_credentials")
    @patch("rotate_tester_passwords.rotate_in_terraform")
    @patch("rotate_tester_passwords.check_for_drift")
    @patch("rotate_tester_passwords.list_all_branches")
    @patch("rotate_tester_passwords.boto3.client")
    @patch(
        "rotate_tester_passwords.get_terraform_output",
        side_effect=["app-123", "sec-123"],
    )
    def test_sync_only_skips_preflight_and_terraform_apply(
        self,
        mock_output,
        mock_boto,
        mock_list,
        mock_drift,
        mock_rotate,
        mock_sync,
    ):
        mock_amplify = MagicMock()
        mock_secrets = MagicMock()
        mock_boto.side_effect = [mock_amplify, mock_secrets]

        assert rot.main(["--sync-only", "tester-1"]) == 0
        mock_list.assert_not_called()
        mock_drift.assert_not_called()
        mock_rotate.assert_not_called()
        mock_sync.assert_called_once_with(
            amplify_client=mock_amplify,
            secrets_client=mock_secrets,
            app_id="app-123",
            secret_id="sec-123",
            indices=["1"],
        )


class TestTerraformProcessErrors:
    @patch("rotate_tester_passwords.get_terraform_output")
    def test_called_process_error_propagates_exit_code(self, mock_output, capsys):
        mock_output.side_effect = subprocess.CalledProcessError(
            returncode=3, cmd=["terraform", "output"]
        )

        assert rot.main(["tester-1"]) == 3
        stderr = capsys.readouterr().err
        assert "Error: Terraform command failed (exit 3)." in stderr


class TestAwsErrorHandling:
    @patch("rotate_tester_passwords.list_all_branches")
    @patch("rotate_tester_passwords.boto3.client")
    @patch(
        "rotate_tester_passwords.get_terraform_output",
        side_effect=["app-123", "sec-123"],
    )
    def test_client_error_exits_cleanly(self, mock_output, mock_boto, mock_list, capsys):
        mock_list.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
            "ListBranches",
        )

        assert rot.main(["tester-1"]) == 1
        stderr = capsys.readouterr().err
        assert "Error:" in stderr
        assert "AccessDeniedException" in stderr

    @patch("rotate_tester_passwords.list_all_branches")
    @patch("rotate_tester_passwords.boto3.client")
    @patch(
        "rotate_tester_passwords.get_terraform_output",
        side_effect=["app-123", "sec-123"],
    )
    def test_botocore_error_exits_cleanly(self, mock_output, mock_boto, mock_list, capsys):
        mock_list.side_effect = NoCredentialsError()

        assert rot.main(["tester-1"]) == 1
        stderr = capsys.readouterr().err
        assert "Error:" in stderr
        assert "Unable to locate credentials" in stderr


class TestCliArguments:
    def test_requires_targets_or_file(self):
        with pytest.raises(SystemExit) as exc:
            rot.main([])
        assert exc.value.code == 2

    def test_sync_only_without_targets_exits_with_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            rot.main(["--sync-only"])
        assert exc.value.code == 2

    def test_rejects_both_targets_and_file(self, tmp_path):
        f = tmp_path / "targets.json"
        f.write_text('["tester-1"]', encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            rot.main(["tester-1", "--targets-file", str(f)])
        assert exc.value.code == 2
