import datetime
import dateutil.parser
import pytz

from tap_tester import runner, menagerie, connections

from base import TestGithubBase


class TestGithubBookmarks(TestGithubBase):
    """Test tap sets a bookmark and respects it for the next sync of a stream"""

    @staticmethod
    def name():
        return "tap_tester_github_bookmarks"

    def calculated_states_by_stream(self, current_state, synced_records, replication_keys):
        """
        Look at the bookmarks from a previous sync and set a new bookmark
        value based off timedelta expectations. This ensures the subsequent sync will replicate
        at least 1 record but, fewer records than the previous sync.
        """
        timedelta_by_stream = {stream: [90,0,0]  # {stream_name: [days, hours, minutes], ...}
                               for stream in self.expected_streams()}

        repo = self.get_properties().get('repository')

        stream_to_calculated_state = {repo: {stream: "" for stream in current_state['bookmarks'][repo].keys()}}
        for stream, state in current_state['bookmarks'][repo].items():
            state_key, state_value = next(iter(state.keys())), next(iter(state.values()))
            sync_messages = [record.get('data') for record in
                                synced_records.get(stream, {'messages': []}).get('messages')
                                if record.get('action') == 'upsert']

            replication_key = next(iter(replication_keys.get(stream)))
            max_record_values = [values.get(replication_key) for values in sync_messages]
            max_value = max(max_record_values)

            new_state_value = min(max_value, state_value)
            state_as_datetime = dateutil.parser.parse(new_state_value)

            days, hours, minutes = timedelta_by_stream[stream]
            calculated_state_as_datetime = state_as_datetime - datetime.timedelta(days=days, hours=hours, minutes=minutes)

            state_format = '%Y-%m-%dT%H:%M:%SZ'
            calculated_state_formatted = datetime.datetime.strftime(calculated_state_as_datetime, state_format)

            stream_to_calculated_state[repo][stream] = {state_key: calculated_state_formatted}

        return stream_to_calculated_state


    def test_run(self):
        """
        • Verify that for each stream you can do a sync which records bookmarks.
        • Verify that the bookmark is the maximum value sent to the target for the replication key.
        • Verify that a second sync respects the bookmark
            All data of the second sync is >= the bookmark from the first sync
            The number of records in the 2nd sync is less then the first
        • Verify that for full table stream, all data replicated in sync 1 is replicated again in sync 2.
        
        PREREQUISITE
        For EACH stream that is incrementally replicated there are multiple rows of data with
            different values for the replication key
        """

        # child_incremental_streams = {'reviews', 'review_comments', 'pr_commits', 'project_cards', 'project_columns'}
        expected_streams = self.expected_streams()
        expected_replication_keys = self.expected_bookmark_keys()
        expected_replication_methods = self.expected_replication_method()

        repo = self.get_properties().get('repository')

        ##########################################################################
        ### First Sync
        ##########################################################################

        conn_id = connections.ensure_connection(self, original_properties=True)

        # Run in check mode
        found_catalogs = self.run_and_verify_check_mode(conn_id)

        # Select only the expected streams tables
        catalog_entries = [ce for ce in found_catalogs if ce['tap_stream_id'] in expected_streams]
        self.perform_and_verify_table_and_field_selection(conn_id, catalog_entries, select_all_fields=True)

        # Run a sync job using orchestrator
        first_sync_record_count = self.run_and_verify_sync(conn_id)
        first_sync_records = runner.get_records_from_target_output()
        first_sync_bookmarks = menagerie.get_state(conn_id)

        ##########################################################################
        ### Update State Between Syncs
        ##########################################################################

        new_states = {'bookmarks': dict()}
        simulated_states = self.calculated_states_by_stream(first_sync_bookmarks,
            first_sync_records, expected_replication_keys)
        for repo, new_state in simulated_states.items():
            new_states['bookmarks'][repo] = new_state
        menagerie.set_state(conn_id, new_states)

        ##########################################################################
        ### Second Sync
        ##########################################################################

        second_sync_record_count = self.run_and_verify_sync(conn_id)
        second_sync_records = runner.get_records_from_target_output()
        second_sync_bookmarks = menagerie.get_state(conn_id)

        ##########################################################################
        ### Test By Stream
        ##########################################################################

        for stream in expected_streams:
            with self.subTest(stream=stream):

                # Expected values
                expected_replication_method = expected_replication_methods[stream]

                # Collect information for assertions from syncs 1 & 2 base on expected values
                first_sync_count = first_sync_record_count.get(stream, 0)
                second_sync_count = second_sync_record_count.get(stream, 0)
                first_sync_messages = [record.get('data') for record in
                                       first_sync_records.get(stream, {'messages': []}).get('messages')
                                       if record.get('action') == 'upsert']
                second_sync_messages = [record.get('data') for record in
                                        second_sync_records.get(stream, {'messages': []}).get('messages')
                                        if record.get('action') == 'upsert']
                first_bookmark_key_value = first_sync_bookmarks.get('bookmarks', {}).get(repo, {stream: None}).get(stream)
                second_bookmark_key_value = second_sync_bookmarks.get('bookmarks', {}).get(repo, {stream: None}).get(stream)


                if expected_replication_method == self.INCREMENTAL:
                    # Collect information specific to incremental streams from syncs 1 & 2
                    replication_key = next(iter(expected_replication_keys[stream]))
                    first_bookmark_value = first_bookmark_key_value.get('since')
                    second_bookmark_value = second_bookmark_key_value.get('since')

                    simulated_bookmark_value = new_states['bookmarks'][repo][stream]['since']

                    # Verify the first sync sets a bookmark of the expected form
                    self.assertIsNotNone(first_bookmark_key_value)
                    self.assertIsNotNone(first_bookmark_key_value.get('since'))

                    # Verify the second sync sets a bookmark of the expected form
                    self.assertIsNotNone(second_bookmark_key_value)
                    self.assertIsNotNone(second_bookmark_key_value.get('since'))

                    # Verify the second sync bookmark is Equal or Greater than the first sync bookmark
                    self.assertGreaterEqual(second_bookmark_value, first_bookmark_value)

                    # Skipping child streams as it's bookmark will be written on the basis of parent streams
                    # and all the child RECORDS will be collected for the updated parents
                    if not self.is_incremental_sub_stream(stream):
                        for record in first_sync_messages:
                            # Verify the first sync bookmark value is the max replication key value for a given stream
                            replication_key_value = record.get(replication_key)
                            
                            self.assertLessEqual(
                                replication_key_value, first_bookmark_value,
                                msg="First sync bookmark was set incorrectly, a record with a greater replication-key value was synced."
                            )

                        for record in second_sync_messages:
                            # Verify the second sync bookmark value is the max replication key value for a given stream
                            replication_key_value = record.get(replication_key)
                            
                            self.assertGreaterEqual(replication_key_value, simulated_bookmark_value,
                                                    msg="Second sync records do not respect the previous bookmark.")
                            
                            self.assertLessEqual(
                                replication_key_value, second_bookmark_value,
                                msg="Second sync bookmark was set incorrectly, a record with a greater replication-key value was synced."
                            )

                    # Verify the number of records in the 2nd sync is less then the first
                    self.assertLessEqual(second_sync_count, first_sync_count)


                elif expected_replication_method == self.FULL:
                    # Verify the syncs do not set a bookmark for full table streams
                    self.assertIsNone(first_bookmark_key_value)
                    self.assertIsNone(second_bookmark_key_value)

                    # Verify the number of records in the second sync is the same as the first
                    self.assertEqual(second_sync_count, first_sync_count)

                else:
                    raise NotImplementedError(
                        "INVALID EXPECTATIONS\t\tSTREAM: {} REPLICATION_METHOD: {}".format(stream, expected_replication_method)
                    )

                # Verify at least 1 record was replicated in the second sync
                self.assertGreater(second_sync_count, 0, msg="We are not fully testing bookmarking for {}".format(stream))
