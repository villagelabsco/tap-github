import collections
import singer
from singer import bookmarks
from tap_github.streams import STREAMS

LOGGER = singer.get_logger()

def get_selected_streams(catalog):
    '''
    Gets selected streams.  Checks schema's 'selected'
    first -- and then checks metadata, looking for an empty
    breadcrumb and mdata with a 'selected' entry
    '''
    selected_streams = []
    for stream in catalog['streams']:
        stream_metadata = stream['metadata']
        for entry in stream_metadata:
            # Stream metadata will have an empty breadcrumb
            if not entry['breadcrumb'] and entry['metadata'].get('selected',None):
                selected_streams.append(stream['tap_stream_id'])

    return selected_streams

def update_currently_syncing(state, stream_name):
    """
    Updates currently syncing stream in the state.
    """
    if stream_name is None and ('currently_syncing' in state):
        del state['currently_syncing']
    else:
        singer.set_currently_syncing(state, stream_name)
    singer.write_state(state)

def update_currently_syncing_repo(state, repo_path):
    """
    Appends repository if completed syncing, 
    and flushes `currently_syncing_repo` when all repositories are synced.
    """
    if (repo_path is None) and ('currently_syncing_repo' in state):
        del state['currently_syncing_repo']
    else:
        state['currently_syncing_repo'] = repo_path
    singer.write_state(state)

def get_ordered_stream_list(currently_syncing):
    """
    Get an ordered list of remaining streams to sync followed by synced streams.
    """
    stream_list = list(sorted(STREAMS.keys()))
    if currently_syncing:
        index = stream_list.index(currently_syncing)
        if currently_syncing:
            stream_list = stream_list[index:] + stream_list[:index]
    return stream_list

def get_ordered_repos(state, repositories):
    """
    Get an ordered list of remaining repos to sync followed by synced repos.
    """
    syncing_repo = state.get("currently_syncing_repo")
    if syncing_repo:
        index = repositories.index(syncing_repo)
        repositories = repositories[index:] + repositories[:index]
    return repositories

def translate_state(state, catalog, repositories):
    '''
    This tap used to only support a single repository, in which case the
    the state took the shape of:
    {
      "bookmarks": {
        "commits": {
          "since": "2018-11-14T13:21:20.700360Z"
        }
      }
    }
    The tap now supports multiple repos, so this function should be called
    at the beginning of each run to ensure the state is translated to the
    new format:
    {
      "bookmarks": {
        "singer-io/tap-adwords": {
          "commits": {
            "since": "2018-11-14T13:21:20.700360Z"
          }
        }
        "singer-io/tap-salesforce": {
          "commits": {
            "since": "2018-11-14T13:21:20.700360Z"
          }
        }
      }
    }
    '''
    nested_dict = lambda: collections.defaultdict(nested_dict)
    new_state = nested_dict()

    for stream in catalog['streams']:
        stream_name = stream['tap_stream_id']
        for repo in repositories:
            if bookmarks.get_bookmark(state, repo, stream_name):
                return state
            if bookmarks.get_bookmark(state, stream_name, 'since'):
                new_state['bookmarks'][repo][stream_name]['since'] = bookmarks.get_bookmark(state, stream_name, 'since')

    return new_state

def get_stream_to_sync(catalog):
    """
    Get the streams for which the sync function should be called(the parent in case of selected child streams).
    """
    streams_to_sync = []
    selected_streams = get_selected_streams(catalog)
    for stream_name, stream_obj in STREAMS.items():
        if stream_name in selected_streams or is_any_child_selected(stream_obj, selected_streams):
            # Append the selected stream or unselected parent stream into the list, if its child or nested child is selected.
            streams_to_sync.append(stream_name)
    return streams_to_sync

def is_any_child_selected(stream_obj,selected_streams):
    """
    Check if any of the child streams is selected for the parent.
    """
    if stream_obj.children:
        for child in stream_obj.children:
            if child in selected_streams:
                return True

            if STREAMS[child].children:
                return is_any_child_selected(STREAMS[child], selected_streams)
    return False

def write_schemas(stream_id, catalog, selected_streams):
    """
    Write the schemas for each stream.
    """
    stream_obj = STREAMS[stream_id]()

    if stream_id in selected_streams:
        # Get catalog object for particular stream.
        stream = [cat for cat in catalog['streams'] if cat['tap_stream_id'] == stream_id ][0]
        singer.write_schema(stream_id, stream['schema'], stream['key_properties'])

    for child in stream_obj.children:
        write_schemas(child, catalog, selected_streams)

def sync(client, config, state, catalog):
    """
    Sync selected streams.
    """

    start_date = config['start_date']

    # Get selected streams, make sure stream dependencies are met
    selected_stream_ids = get_selected_streams(catalog)
    streams_to_sync = get_stream_to_sync(catalog)
    LOGGER.info('Sync stream %s', streams_to_sync)

    repositories = list(sorted(client.extract_repos_from_config()))

    state = translate_state(state, catalog, repositories)
    singer.write_state(state)

    # pylint: disable=too-many-nested-blocks
    for repo in get_ordered_repos(state, repositories):
        LOGGER.info("Starting sync of repository: %s", repo)
        currently_syncing = state.get('currently_syncing')
        update_currently_syncing_repo(state, repo)

        for stream_id in get_ordered_stream_list(currently_syncing):
            stream_obj = STREAMS[stream_id]()

            # If it is a "sub_stream", it will be synced as part of parent stream
            if stream_id in streams_to_sync and not stream_obj.parent:
                write_schemas(stream_id, catalog, selected_stream_ids)
                update_currently_syncing(state, stream_id)

                state = stream_obj.sync_endpoint(client = client,
                                                 state = state,
                                                 catalog = catalog['streams'],
                                                 repo_path = repo,
                                                 start_date = start_date,
                                                 selected_stream_ids = selected_stream_ids,
                                                 stream_to_sync = streams_to_sync
                                                )

                singer.write_state(state)
        update_currently_syncing(state, None)
    update_currently_syncing_repo(state, None)
