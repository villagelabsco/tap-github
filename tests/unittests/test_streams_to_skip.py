import unittest
from unittest import mock
from tap_github.sync import get_streams_to_skip, sync


def get_stream_catalog(stream_name, is_selected=False):
    """Return catalog for stream"""
    return {
        "schema": {},
        "tap_stream_id": stream_name,
        "metadata": [
            {
                "breadcrumb": [],
                "metadata": {
                    "selected": is_selected
                }
            }
        ],
        "key_properties": []
    }


class TestGetStreamsToSkip(unittest.TestCase):
    """Test get_streams_to_skip function"""

    def test_no_config(self):
        """No streams_to_skip in config returns empty set"""
        result = get_streams_to_skip({})
        self.assertEqual(result, set())

    def test_empty_list(self):
        """Empty streams_to_skip list returns empty set"""
        result = get_streams_to_skip({"streams_to_skip": []})
        self.assertEqual(result, set())

    def test_single_stream_no_children(self):
        """Skipping a stream with no children returns just that stream"""
        result = get_streams_to_skip({"streams_to_skip": ["commits"]})
        self.assertEqual(result, {"commits"})

    def test_parent_skips_children(self):
        """Skipping a parent stream also skips its children"""
        result = get_streams_to_skip({"streams_to_skip": ["pull_requests"]})
        self.assertEqual(result, {"pull_requests", "reviews", "review_comments", "pr_commits"})

    def test_nested_children(self):
        """Skipping a parent with nested children skips all descendants"""
        result = get_streams_to_skip({"streams_to_skip": ["teams"]})
        self.assertEqual(result, {"teams", "team_members", "team_memberships"})

    def test_projects_hierarchy(self):
        """Skipping projects skips columns and cards"""
        result = get_streams_to_skip({"streams_to_skip": ["projects"]})
        self.assertEqual(result, {"projects", "project_columns", "project_cards"})

    def test_multiple_streams(self):
        """Skipping multiple streams works correctly"""
        result = get_streams_to_skip({"streams_to_skip": ["commits", "events", "collaborators"]})
        self.assertEqual(result, {"commits", "events", "collaborators"})

    def test_coworker_light_typical_config(self):
        """Typical Coworker Light config skips high-permission streams"""
        result = get_streams_to_skip({
            "streams_to_skip": ["commits", "commit_comments", "collaborators", "teams", "projects"]
        })
        expected = {
            "commits", "commit_comments", "collaborators",
            "teams", "team_members", "team_memberships",
            "projects", "project_columns", "project_cards",
        }
        self.assertEqual(result, expected)


@mock.patch("singer.write_state")
@mock.patch("tap_github.sync.write_schemas")
@mock.patch("tap_github.streams.IncrementalGithubStream.sync_endpoint")
class TestSyncWithStreamsToSkip(unittest.TestCase):
    """Test that sync respects streams_to_skip config"""

    @mock.patch("tap_github.streams.IncrementalOrderedGithubStream.sync_endpoint")
    def test_skipped_streams_not_synced(self, mock_inc_ordered, mock_incremental, mock_write_schemas, mock_write_state):
        """Streams in streams_to_skip should not be synced"""
        mock_catalog = {"streams": [
            get_stream_catalog("commits", True),
            get_stream_catalog("events", True),
            get_stream_catalog("issues", True),
        ]}

        client = mock.Mock()
        client.extract_repos_from_config.return_value = (["test-repo"], set())
        client.authed_get_all_pages.return_value = []
        client.not_accessible_repos = set()

        config = {"start_date": "2024-01-01T00:00:00Z", "streams_to_skip": ["commits"]}
        sync(client, config, {}, mock_catalog)

        # write_schemas should be called for events and issues but NOT commits
        schema_calls = [call[0][0] for call in mock_write_schemas.call_args_list]
        self.assertNotIn("commits", schema_calls)
        self.assertIn("events", schema_calls)
        self.assertIn("issues", schema_calls)

    @mock.patch("tap_github.streams.IncrementalOrderedGithubStream.sync_endpoint")
    def test_no_skip_config_syncs_all(self, mock_inc_ordered, mock_incremental, mock_write_schemas, mock_write_state):
        """Without streams_to_skip, all selected streams are synced"""
        mock_catalog = {"streams": [
            get_stream_catalog("commits", True),
            get_stream_catalog("events", True),
        ]}

        client = mock.Mock()
        client.extract_repos_from_config.return_value = (["test-repo"], set())
        client.authed_get_all_pages.return_value = []
        client.not_accessible_repos = set()

        config = {"start_date": "2024-01-01T00:00:00Z"}
        sync(client, config, {}, mock_catalog)

        schema_calls = [call[0][0] for call in mock_write_schemas.call_args_list]
        self.assertIn("commits", schema_calls)
        self.assertIn("events", schema_calls)
