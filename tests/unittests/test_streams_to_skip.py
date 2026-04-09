import unittest
from unittest.mock import patch, MagicMock
from tap_github.sync import get_streams_to_skip


class TestGetStreamsToSkip(unittest.TestCase):
    """Tests for the get_streams_to_skip function."""

    def test_empty_config(self):
        """No streams_to_skip in config returns empty set."""
        result = get_streams_to_skip({})
        self.assertEqual(result, set())

    def test_empty_list(self):
        """Empty streams_to_skip list returns empty set."""
        result = get_streams_to_skip({"streams_to_skip": []})
        self.assertEqual(result, set())

    def test_single_stream_no_children(self):
        """Skipping a stream with no children returns just that stream."""
        result = get_streams_to_skip({"streams_to_skip": ["commits"]})
        self.assertIn("commits", result)

    def test_parent_skips_children(self):
        """Skipping a parent stream also skips its children."""
        # teams has children: team_members -> team_memberships
        result = get_streams_to_skip({"streams_to_skip": ["teams"]})
        self.assertIn("teams", result)
        self.assertIn("team_members", result)
        self.assertIn("team_memberships", result)

    def test_nested_children(self):
        """Skipping pull_requests also skips reviews, review_comments, pr_commits."""
        result = get_streams_to_skip({"streams_to_skip": ["pull_requests"]})
        self.assertIn("pull_requests", result)
        self.assertIn("reviews", result)
        self.assertIn("review_comments", result)
        self.assertIn("pr_commits", result)

    def test_projects_children(self):
        """Skipping projects also skips project_columns and project_cards."""
        result = get_streams_to_skip({"streams_to_skip": ["projects"]})
        self.assertIn("projects", result)
        self.assertIn("project_columns", result)
        self.assertIn("project_cards", result)

    def test_multiple_streams(self):
        """Multiple streams can be skipped at once."""
        result = get_streams_to_skip({
            "streams_to_skip": ["commits", "collaborators", "teams"]
        })
        self.assertIn("commits", result)
        self.assertIn("collaborators", result)
        self.assertIn("teams", result)
        self.assertIn("team_members", result)
        self.assertIn("team_memberships", result)

    def test_unknown_stream_ignored(self):
        """Unknown stream names are included but don't cause errors."""
        result = get_streams_to_skip({"streams_to_skip": ["nonexistent_stream"]})
        self.assertIn("nonexistent_stream", result)

    def test_typical_coworker_light_config(self):
        """Typical Coworker Light config skips expected streams."""
        result = get_streams_to_skip({
            "streams_to_skip": [
                "commits", "commit_comments", "collaborators",
                "teams", "projects"
            ]
        })
        expected = {
            "commits", "commit_comments", "collaborators",
            "teams", "team_members", "team_memberships",
            "projects", "project_columns", "project_cards",
        }
        self.assertEqual(result, expected)

    def test_child_stream_only(self):
        """Skipping only a child stream does not skip the parent."""
        result = get_streams_to_skip({"streams_to_skip": ["team_members"]})
        self.assertIn("team_members", result)
        self.assertIn("team_memberships", result)
        self.assertNotIn("teams", result)


class TestSyncWithStreamsToSkip(unittest.TestCase):
    """Tests that sync respects streams_to_skip."""

    @patch("tap_github.sync.get_stream_to_sync")
    @patch("tap_github.sync.get_selected_streams")
    @patch("tap_github.sync.translate_state")
    @patch("tap_github.sync.do_sync")
    @patch("tap_github.sync.update_currently_syncing_repo")
    @patch("singer.write_state")
    def test_sync_filters_skipped_streams(
        self, mock_write_state, mock_update_repo, mock_do_sync,
        mock_translate, mock_selected, mock_stream_to_sync
    ):
        """Skipped streams are removed from selected_stream_ids and streams_to_sync."""
        mock_selected.return_value = ["commits", "issues", "teams", "team_members"]
        mock_stream_to_sync.return_value = ["commits", "issues", "teams", "team_members"]
        mock_translate.return_value = {"bookmarks": {}}

        client = MagicMock()
        client.extract_repos_from_config.return_value = (["org/repo"], ["org"])
        client.not_accessible_repos = set()

        config = {
            "start_date": "2020-01-01T00:00:00Z",
            "streams_to_skip": ["commits", "teams"],
        }
        catalog = {"streams": []}

        from tap_github.sync import sync
        sync(client, config, {"bookmarks": {}}, catalog)

        # Verify do_sync was called with filtered streams
        for call_args in mock_do_sync.call_args_list:
            args = call_args[0]
            streams_to_sync_arg = args[1]
            selected_ids_arg = args[2]
            # None of the skipped streams should be present
            for skipped in ["commits", "teams", "team_members", "team_memberships"]:
                self.assertNotIn(skipped, streams_to_sync_arg)
                self.assertNotIn(skipped, selected_ids_arg)

    @patch("tap_github.sync.get_stream_to_sync")
    @patch("tap_github.sync.get_selected_streams")
    @patch("tap_github.sync.translate_state")
    @patch("tap_github.sync.do_sync")
    @patch("tap_github.sync.update_currently_syncing_repo")
    @patch("singer.write_state")
    def test_sync_no_skip_syncs_all(
        self, mock_write_state, mock_update_repo, mock_do_sync,
        mock_translate, mock_selected, mock_stream_to_sync
    ):
        """Without streams_to_skip, all selected streams are synced."""
        mock_selected.return_value = ["commits", "issues"]
        mock_stream_to_sync.return_value = ["commits", "issues"]
        mock_translate.return_value = {"bookmarks": {}}

        client = MagicMock()
        client.extract_repos_from_config.return_value = (["org/repo"], ["org"])
        client.not_accessible_repos = set()

        config = {"start_date": "2020-01-01T00:00:00Z"}
        catalog = {"streams": []}

        from tap_github.sync import sync
        sync(client, config, {"bookmarks": {}}, catalog)

        for call_args in mock_do_sync.call_args_list:
            args = call_args[0]
            selected_ids_arg = args[2]
            self.assertIn("commits", selected_ids_arg)
            self.assertIn("issues", selected_ids_arg)
