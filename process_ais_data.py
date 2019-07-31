"""Preprocesses AIS data.

To obtain the needed AIS data, see (and run) ``get_raw.sh``.

Uses information specified in the ``config_file`` to chew through available AIS csv data to generate an output csv file
with the discretized states, inferred actions, and records the resulting grid parameters in the ``meta_file``.
"""
import yaml
import os
import math
import datetime
import numpy as np
import pandas as pd


def main():
    """Driver code to run the big steps of pre-processing the data.

    First, all the script parameters are loaded by reading the ``.yaml`` ``config_file`` and this config is unpacked.
    All the csv files within a specified directory are found and returned as ``csv_files``, along with each file's
    year, month, and zone in ``all_files_meta``. Then, these csv files are read and organized into a large
    set of trajectories ordered by id (mmsi). Finally, these trajectories are discretized before being written into
    an output csv containing only rows of id-state-action-state transitions. Another yaml file is written to
    ``meta_file`` to specify the final grid parameters, output directories, and the year, month, and zone of
    all the files read in.
    """
    # file containing important options, directories, parameters, etc.
    config_file = 'config.yaml'

    # file to write final grid_params and the csv files' respective years, months, and zones
    meta_file = 'meta_data.yaml'

    # gets the config dictionary and unpacks it
    config = get_config(config_file)
    options = config['options']
    directories = config['directories']
    meta_params = config['meta_params']
    grid_params = config['grid_params']

    # gets the csv files available and their metadata
    csv_files, all_files_meta = collect_csv_files(options, directories, meta_params)

    # reads the collected csv files and assembles trajectories
    trajectories, grid_params = read_data(csv_files, options, grid_params)

    # processes (fits to grid) trajectories and writes generates sequences to output file
    write_data(trajectories, options, directories, grid_params)

    # writes file metadata, paths, and grid parameters to ``meta_file``
    directories_out = {'in_dir_path': directories['out_dir_path'], 'in_dir_data': directories['out_dir_file']}
    out_dict = {'all_files_meta': all_files_meta, 'directories': directories_out, 'grid_params': grid_params}
    with open(meta_file, 'w') as outfile:
        yaml.dump(out_dict, outfile, default_flow_style=False)


def get_config(config_file):
    """Helper function to get dictionary of script parameters.

    Mostly boilerplate code to read in ``config_file`` as a ``.yaml`` file that specifies important script parameters.
    Upon success, this config dictionary is returned to main to be unpacked.

    Args:
        config_file: The name of the ``.yaml`` file containing the script configuration parameters, located in the
            directory the script is run.

    Returns:
        A dictionary of script configuration parameters.
    """
    with open(config_file, 'r') as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)


def collect_csv_files(options, directories, meta_params):
    """Traverses the directory containing the decompressed AIS data to get the CSV names for further processing.

    Uses the os library to find all csv files within the directory defined by ``directories``, populating the
    ``csv_files`` list for later reading of all valid csvs found and logging file metadata in ``all_files_meta``.

    Args:
        options: The options specified by the user in ``config_file`` on how to run the script.
        directories: The directories specified by the user in ``config_file`` on where to look for input data, where to
            write output data, and what those files should be named.
        meta_params: The parameters specified by the user in ``config_file`` to define time and zone range for the
            AIS data.

    Returns:
        csv_files: A list of paths to all valid csv files found.
        all_files_meta: A dictionary listing the year, month, and zone corresponding to each csv file's data.
    """
    # initialize a list that will be filled with all csv file names from root
    csv_files = []
    all_files_meta = {}
    for root, dirs, files in os.walk(directories['in_dir_path'] + directories['in_dir_data']):
        for file in files:
            if file.endswith(".csv"):
                year, month, zone = get_meta_data(file)  # finds the data year, month, and zone based on the file name
                # only considers valid years and months if time is bounded and valid zones if zones are bounded
                if (not options['bound_time'] or (meta_params['min_year'] <= year <= meta_params['max_year'])) and \
                   (not options['bound_zone'] or (meta_params['min_zone'] <= zone <= meta_params['max_zone'])):
                    if (not options['bound_time'] or (not year == meta_params['min_year'] or month >= meta_params['min_month'])) and \
                       (not options['bound_time'] or (not year == meta_params['max_year'] or month <= meta_params['max_month'])):
                        # csv_files will contain file locations relative to current directory
                        csv_files.append(os.path.join(root, file))

                        # create dictionary to describe file characteristics
                        file_meta = {'year': year, 'month': month, 'zone': zone}
                        all_files_meta[file] = file_meta

    return csv_files, all_files_meta


def read_data(csv_files, options, grid_params):
    """Iterate through each csv file to segregate each trajectory by its mmsi id.

    Reads each csv in ``csv_files`` to obtain coordinates and timestamp series associated with each mmsi id encountered.
    Optionally, the boundaries of the grid later specified can be inferred by calculating the minimum and maximum
    longitudes and latitudes by setting ``options['bound_lon']`` and ``options['bound_lat']`` to False, respectively
    in the ``config_file``. It can also be specified to only read the first ``options['MAX_ROWS']`` of each csv file by
    setting ``options['limit_rows']`` to True in the ``config_file``.

    Args:
        csv_files: A list of paths to all valid csv files found.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset. Will be updated to fit
            dataset if ``options['bound_lon']`` or ``options['bount_lat']`` is False in the ``config_file``.

    Returns:
        trajectories: A pandas DataFrame of all data entries with the format ``['MMSI', 'LON', 'LAT', 'TIME']``.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset. Will be updated to fit
            dataset if ``options['bound_lon']`` or ``options['bount_lat']`` is False in the ``config_file``.
    """
    # overwrite hard boundaries on longitude and latitude if not bounded in config.yaml
    if not options['bound_lon']:
        grid_params['min_lon'] = 180
        grid_params['max_lon'] = -180
    if not options['bound_lat']:
        grid_params['min_lat'] = 90
        grid_params['max_lat'] = -90

    # holds all ais data, one dataframe per csv file
    ais_data = []

    # get data from all csv files
    for csv_file in csv_files:
        # reads in the raw data with columns and number of rows specified in config.yaml
        nrows = options['MAX_ROWS'] if options['limit_rows'] else None
        usecols = ['MMSI', 'LON', 'LAT', 'BaseDateTime']
        ais_df = pd.read_csv(csv_file, usecols=usecols, nrows=nrows)
        ais_df = ais_df[usecols]

        # Interprets raw time entries as timestamps and drops original column
        ais_df['TIME'] = ais_df['BaseDateTime'].apply(get_time)
        ais_df.drop(columns='BaseDateTime', inplace=True)

        # keeps only rows in boundaries if specified
        if options['bound_lon']:
            ais_df = ais_df.loc[(ais_df['LON'] >= grid_params['min_lon']) & (ais_df['LON'] <= grid_params['max_lon'])]
        if options['bound_lat']:
            ais_df = ais_df.loc[(ais_df['LAT'] >= grid_params['min_lat']) & (ais_df['LAT'] <= grid_params['max_lat'])]

        # infers grid boundaries if no boundaries are specified
        if not options['bound_lon'] and ais_df['LON'].min() < grid_params['min_lon']:
            grid_params['min_lon'] = ais_df['LON'].min()
        if not options['bound_lon'] and ais_df['LON'].max() > grid_params['max_lon']:
            grid_params['max_lon'] = ais_df['LON'].max()
        if not options['bound_lat'] and ais_df['LAT'].min() < grid_params['min_lat']:
            grid_params['min_lat'] = ais_df['LAT'].min()
        if not options['bound_lat'] and ais_df['LAT'].max() > grid_params['max_lat']:
            grid_params['max_lat'] = ais_df['LAT'].max()

        # appends current dataframe to list of all dataframes
        ais_data.append(ais_df)

    # merges dataframes from all csvs
    trajectories = pd.concat(ais_data, axis=0, ignore_index=True)

    # rounds inferred grid boundaries to nearest degree to provide some padding to each boundary
    if not options['bound_lon']:
        grid_params['min_lon'] = float(math.floor(grid_params['min_lon']))
        grid_params['max_lon'] = float(math.ceil(grid_params['max_lon']))
    if not options['bound_lat']:
        grid_params['min_lat'] = float(math.floor(grid_params['min_lat']))
        grid_params['max_lat'] = float(math.ceil(grid_params['max_lat']))
    # number of columns in the resulting grid
    grid_params['num_cols'] = math.ceil((grid_params['max_lon'] - grid_params['min_lon']) / grid_params['grid_len'])

    return trajectories, grid_params


def write_data(trajectories, options, directories, grid_params):
    """Writes all trajectories to an output csv file using a discretized state and action grid.

    Uses the trajectories variable to look at each id-state-action-state transition to discretize all states and to
    interpolate actions if specified. These discretized states with interpolated or arbitrary actions are then written
    to the output csv specified by ``options['out_dir'] + options['out_file']``. Each trajectory is also sorted by
    its timestamp.

    The trajectories have their ids aliased by a counter variable that only increments whenever a trajectory is
    going to appear in the final csv. Self-transitions are discarded, and because of the huge grid size, most
    trajectories will be discarded since they will never transition between grid squares.

    Args:
        trajectories: A pandas DataFrame of all data entries with the format ``['MMSI', 'LON', 'LAT', 'TIME']``.
        options: The options specified by the user in ``config_file`` on how to run the script.
        directories: The directories specified by the user in ``config_file`` on where to look for input data, where to
            write output data, and what those files should be named.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.
    """
    # sorts based on MMSI, then sorts by timestamps within MMSI groups, drops the time column
    trajectories.sort_values(['MMSI', 'TIME'], inplace=True)
    trajectories.drop(columns='TIME', inplace=True)

    # creates a new column of discretized states based on coordinate pairs
    trajectories['STATE'] = trajectories.apply(lambda x: get_state(x['LON'], x['LAT'], grid_params), axis=1)

    # looks at state differences within MMSI trajectories and only keeps the states with nonzero differences
    # trajectories with only one state are kept because they will have a first row with 'nan' for diff
    non_self_transitions = trajectories['STATE'].groupby(trajectories['MMSI']).diff().ne(0)
    trajectories = trajectories.loc[non_self_transitions]

    # rounds latitude and longitude to specified precision
    trajectories = trajectories.round({'LON': options['prec_coords'], 'LAT': options['prec_coords']})

    # drops the trajectories with fewer states than ``options['MIN_STATES']``
    traj_lengths = trajectories['MMSI'].value_counts()
    traj_keep = traj_lengths[traj_lengths > options['MIN_STATES'] - 1].index.values
    trajectories = trajectories.loc[trajectories['MMSI'].isin(traj_keep)]

    # aliases the MMSI column to ascending integers to enumerate trajectories and make easier to read
    alias = {mmsi: ind for ind, mmsi in enumerate(trajectories['MMSI'].unique())}
    trajectories.replace({"MMSI": alias}, inplace=True)

    # resets index now that manipulation of this dataframe has finished
    trajectories.reset_index(drop=True, inplace=True)

    # creates a series of stacked dataframes, each dataframe representing an interpolated state transition
    sas = trajectories.groupby('MMSI').apply(lambda x: get_action(x, options, grid_params))
    if isinstance(sas, pd.DataFrame):  # becomes a DataFrame when every trajectory has only one sas triplet
        sas = sas[0]

    # merges the series of stacked dataframes into one dataframe and resets the index
    sas = pd.concat(sas.tolist())
    sas.reset_index(drop=True, inplace=True)

    # writes new dataframe to final csv
    sas.to_csv(directories['out_dir_path'] + directories['out_dir_file'], index=False)


def get_bounds(zone):
    """Helper function to get longitude boundaries corresponding to zone.

    Calculates the minimum and maximum longitudes corresponding to an integer zone
    representing a Universal Transverse Mercator coordinate system zone. Each zone is
    6 degrees wide, dividing the Earth into 60 zones, starting with zone 1 at 180 deg W. This function
    also wraps the zone with a modulo operator, so zone -1 would map to zone 58.

    Args:
        zone: An integer representing the Universal Transverse Mercator coordinate system zone.

    Returns:
        A tuple ``(min_lon, max_lon)`` of floats corresponding to the minimum and maximum longitudes
        of the zone passed in.
    """
    min_lon = (6. * ((zone - 1) % 60)) - 180.  # counts 6 degrees per zone, offset by -180
    return min_lon, (min_lon + 6.)


def get_meta_data(file_name):
    """Helper function to retrieve a given file name's year, month, and zone.

    Takes a string file_name formatted as ``'AIS_yyyy_mm_Zone##.csv'`` and returns the numerical
    values of ``yyyy, mm, ##`` corresponding to year, month, and zone number as a tuple.

    Args:
        file_name: a string formatted as ``'AIS_yyyy_mm_Zone##.csv'``.

    Returns:
        A tuple ``(year, month, zone)`` of integers corresponding the year, month, and zone of the file passed in.
    """
    meta_file_data = file_name.split('_')  # splits csv file on '_' character, which separates relevant file info
    year = int(meta_file_data[-3])  # third to last element of file is the year
    month = int(meta_file_data[-2])  # second to last element of file is the month

    # get zone number for csv file being read
    ending_raw = meta_file_data[-1]  # gets last part of the file, with format "ZoneXX.csv"
    ending_data = ending_raw.split('.')  # splits last part of file on '.' character
    zone_raw = ending_data[0]  # gets "ZoneXX" string
    zone_data = zone_raw[-2:]  # gets last 2 characters of "ZoneXX" string - will be the zone number
    zone = int(zone_data)
    return year, month, zone


def get_time(time_str):
    """Uses the datetime library to calculate a timestamp object from a ``time_str``.

    Takes in a timestamp string, breaks it down into its components, and then calls the datetime library
    to construct a datetime object for easier manipulation and time calculations between data entries.

    Args:
        time_str: A timestamp string formatted as 'YYYY-MM-DDTHH:MM:SS'.

    Returns:
        A timestamp object corresponding exactly to the timestamp string passed in.
    """
    date, time = time_str.split('T')
    year, month, day = date.split('-')
    hour, minute, second = time.split(':')
    dt = datetime.datetime(int(year), int(month), int(day), hour=int(hour), minute=int(minute), second=int(second))
    return dt.timestamp()


def get_state(cur_lon, cur_lat, grid_params):
    """Discretizes a coordinate pair into its state space representation in a Euclidean grid.

    Takes in a coordinate pair ``cur_lon, cur_lat`` and grid parameters ``min_lon, min_lat, num_cols, grid_len``
    to calculate the integer state representing the given coordinate pair. This coordinate grid is always row-major.
    ``min_lon, min_lat`` represent the bottom-left corner of the grid.

    For example, a 3 x 4 grid would have the following state enumeration pattern:

    8 9 10 11      (min_lon, min_lat + grid_len)              (min_lon + grid_len, min_lat + grid_len)
    4 5 6 7                        |
    0 1 2 3              (min_lon, min_lat) ---------------------- (min_lon + grid_len, min_lon)

    In this example, the top left of state 0's boundaries would be the point ``min_lon, min_lat``, and the total area
    mapping to state 0 would be the square with ``min_lon, min_lat`` as the top left corner and each side of the
    square with length ``grid_len``. The inclusive parts of the square's boundaries mapping to zero are solid lines.

    Args:
        cur_lon: the longitude of the data point represented as a float.
        cur_lat: the latitude of the data point represented as a float.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        An integer state corresponding to the discretized representation of cur_lon, cur_lat according to the
        grid parameters passed in.
    """
    # normalize lat and lon to the minimum values
    cur_lon -= grid_params['min_lon']
    cur_lat -= grid_params['min_lat']
    # find the row and column position based on grid_len
    col = cur_lon // grid_params['grid_len']
    row = cur_lat // grid_params['grid_len']
    # find total state based on num_cols in final grid
    return int(row * grid_params['num_cols'] + col)


def get_action(traj, options, grid_params):
    """Wrapper function for other ``get_action`` functions.

    Calls the correct ``get_action`` variant based on the options input and returns the resulting output with
    interpolated actions for all entries in the series.

    Args:
        traj: A pandas DataFrame with all the states encountered in a trajectory with their respective coordinates.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        A pandas Series of DataFrames with each DataFrame entry representing one interpolated state-action-state
        triplet.
    """
    # retrieves trajectory data
    traj_num = traj.name
    states = traj['STATE']
    lon = traj['LON']
    lat = traj['LAT']

    # prepares a dictionary of state transitions to be fed row-by-row as a DataFrame to the interpolation functions
    data = {
        'ID': pd.Series([traj_num] * (len(states) - 1)),
        'PREV': states.iloc[:-1].reset_index(drop=True),
        'CUR': states.iloc[1:].reset_index(drop=True)
    }

    # if specified, appends the original entry coordinates (not discretized) for each 'PREV' entry
    if options['append_coords']:
        data['LON'] = lon[:-1].reset_index(drop=True)
        data['LAT'] = lat[:-1].reset_index(drop=True)

    # formats the final data dictionary as a DataFrame
    traj_df = pd.DataFrame(data)

    # selects specified interpolation function and applies it row-wise to ``traj_df``
    if not options['interp_actions']:
        traj_df = traj_df.apply(lambda x: get_action_arb(x, options, grid_params), axis=1)
    else:
        if options['allow_diag']:
            traj_df = traj_df.apply(lambda x: get_action_interp_with_diag(x, options, grid_params), axis=1)
        else:
            traj_df = traj_df.apply(lambda x: get_action_interp_reg(x, options, grid_params), axis=1)

    # appends the final state to each trajectory as its own row to allow for easier plotting of trajectories
    if options['append_coords']:
        final_state = {'ID': traj_num, 'PREV': states.iloc[-1], 'ACT': -1, 'CUR': -1, 'LON': lon.iloc[-1],
                       'LAT': lat.iloc[-1]}
        last_row = pd.DataFrame(final_state, index=[0, ])
        traj_df = pd.concat([traj_df, pd.Series(data={0: last_row})], ignore_index=True)

    return traj_df


def get_action_arb(row, options, grid_params):
    """Calculates an arbitrary action from the previous state to current state relative to the previous state.

    First, the relative offset between the current and previous state in rows and columns is calculated.
    The action is then calculated according to a spiral rule beginning with the previous state, so self-transitions
    are defined as 0 as an initial condition. Spiral inspired by the polar function r = theta.

    For example, if ``prev_state = 5``, ``cur_state = 7``, and ``num_cols = 4``, then our state grid is populated
    as follows:

    8  9 10 11
    4  p  6  c
    0  1  2  3

    Where p represents the location of the previous state, and c represents the location of the current state.
    Then the current state's position relative to the previous state is ``rel_row = 0``, ``rel_col = 2``. Our action
    spiral then looks like this:

    15 14 13 12 11      15 14 13 12 11
    16  4  3  2 10      16  4  3  2 10
    17  5  0  1  9  ->  17  5  p  1  c
    18  6  7  8 24      18  6  7  8 24
    19 20 21 22 23      19 20 21 22 23

    Thus, this algorithm will return 9 as the action.

    Args:
        row: a pandas Series representing one row of the DataFrame the function is applied to, containing the trajectory
            number, previous state, current state, longitude, and latitude.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        A pandas DataFrame of transitions that interpolate between ``prev_state`` and ``cur_state``. Optionally appends
        the coordinate values of each ``prev_state`` as additional columns.
    """
    # retrieves transition data
    traj_num = int(row['ID'])
    prev_state = int(row['PREV'])
    cur_state = int(row['CUR'])
    num_cols = grid_params['num_cols']

    # gets row, column decomposition for previous and current states
    prev_row = prev_state // num_cols
    prev_col = prev_state % num_cols
    cur_row = cur_state // num_cols
    cur_col = cur_state % num_cols
    # calculates current state's position relative to previous state
    rel_row = cur_row - prev_row
    rel_col = cur_col - prev_col

    # simple routine to calculate a spiral set of actions
    # the sequence defined by layer corresponds to the total number of grid squares in each spiral layer
    action_num = x = y = i = 0
    layer = (2 * i + 1) ** 2  # sets breakpoint for when to increment i
    while not(x == rel_col and y == rel_row):
        if action_num == layer - 1:
            i += 1  # move to next spiral
            x = i
            layer = (2 * i + 1) ** 2  # calculate breakpoint for next spiral
        elif x == i and y < i:  # traverses from beginning of layer to top right corner
            y += 1
        elif x > -i and y == i:  # traverses from top right to top left corner
            x -= 1
        elif x == -i and y > -i:  # traverses from top left to bottom left corner
            y -= 1
        elif x < i and y == -i:  # traverses from bottom left to bottom right corner
            x += 1
        elif x == i and y < 0:  # traverses from bottom left corner to end of layer
            y += 1
        action_num += 1

    # prepares final data dictionary to build DataFrame
    out_data = {'ID': [traj_num, ], 'PREV': [prev_state, ], 'ACT': [action_num, ], 'CUR': [cur_state, ]}

    # overwrites the coordinates of the first state in interpolated transitions to be original raw values
    if options['append_coords']:
        out_data['LON'] = [row['LON'], ]
        out_data['LAT'] = [row['LAT'], ]

    # returns final output as DataFrame
    out_df = pd.DataFrame(out_data)
    return out_df


def get_action_interp_with_diag(row, options, grid_params):
    """Calculates the actions taken from the previous state to reach the current state, interpolating if necessary.

    First, the relative offset between the current and previous state in rows and columns is calculated.
    Then the sign of ``rel_row`` and ``rel_col`` are then used to iteratively describe a sequence of actions
    from the previous state to current state, breaking up state transitions with multiple actions if
    the states are not adjacent (including diagonals, resulting in 9 possible actions). This interpolation
    assumes a deterministic system.

    For example, if ``prev_state = 5``, ``cur_state = 7``, and ``num_cols = 4``, then our state grid is populated
    as follows:

    8  9 10 11
    4  p  6  c
    0  1  2  3

    output: ``pd.DataFrame({})``

    Where p represents the location of the previous state, and c represents the location of the current state.
    Then the current state's position relative to the previous state is ``rel_row = 0``, ``rel_col = 2``. Our
    action spiral then looks like this:

    4  3  2      4  3  2
    5  0  1  ->  5  p  1  c
    7  8  9      6  7  8

    output: ``pd.DataFrame({
                            'ID': [traj_num, ],
                            'PREV': [prev_state, ],
                            'ACT': [1, ],
                            'CUR': [prev_state + 1, ]
                            })``

    Because the current state is not adjacent (including diagonals), we interpolate by taking the action that
    brings us closest to the current state: action 1, resulting in a new action spiral and a new previous state.

    4  3  2      4  3  2
    5  0  1  ->  5  p  c
    7  8  9      6  7  8

    output: ``pd.DataFrame({
                            'ID': [traj_num] * 2,
                            'PREV': [prev_state, prev_state + 1],
                            'ACT': [1, 1],
                            'CUR': [prev_state + 1, cur_state]
                            })``

    Now, our new previous state is adjacent to the current state, so we can take action 1, which updates our
    previous state to exactly match the current state, so the algorithm terminates and returns the list of
    state-action-state transitions.

    Args:
        row: a pandas Series representing one row of the DataFrame the function is applied to, containing the trajectory
        number, previous state, current state, longitude, and latitude.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        A pandas DataFrame of transitions that interpolate between ``prev_state`` and ``cur_state``. Optionally
        appends the coordinate values of each ``prev_state`` as additional columns.
    """
    # retrieves transition data
    traj_num = int(row['ID'])
    prev_state = int(row['PREV'])
    cur_state = int(row['CUR'])
    num_cols = grid_params['num_cols']

    # instantiate lists to hold column values for final DataFrame output
    prevs = []
    acts = []
    curs = []
    lons = []
    lats = []

    # gets row, column decomposition for previous and current states
    prev_row = prev_state // num_cols
    prev_col = prev_state % num_cols
    cur_row = cur_state // num_cols
    cur_col = cur_state % num_cols
    # calculates current state's position relative to previous state
    rel_row = cur_row - prev_row
    rel_col = cur_col - prev_col

    # write output rows until rel_row and rel_col are both zero
    # out_rows = []
    while not(rel_row == 0 and rel_col == 0):
        # selects action to minimize rel_row and rel_col
        action = -1
        if rel_row > 0 and rel_col > 0:
            action = 2
        elif rel_row > 0 and rel_col == 0:
            action = 3
        elif rel_row > 0 and rel_col < 0:
            action = 4
        elif rel_row == 0 and rel_col > 0:
            action = 1
        elif rel_row == 0 and rel_col < 0:
            action = 5
        elif rel_row < 0 and rel_col > 0:
            action = 8
        elif rel_row < 0 and rel_col == 0:
            action = 7
        elif rel_row < 0 and rel_col < 0:
            action = 6

        # moves rel_row and rel_col in the opposite directions of their signs
        row_diff = -np.sign(rel_row)
        col_diff = -np.sign(rel_col)

        # updates states and relative row, column based on action selected
        rel_row += row_diff
        rel_col += col_diff
        temp_row = prev_row - row_diff
        temp_col = prev_col - col_diff
        temp_state = temp_row * num_cols + temp_col
        prev_state = prev_row * num_cols + prev_col

        # records an interpolated state-action-state transition
        prevs.append(prev_state)
        acts.append(action)
        curs.append(temp_state)

        # gets the coordinates of the interpolated state - will be the coordinates of the middle of the state
        if options['append_coords']:
            lon, lat = state_to_coord(prev_state, options, grid_params)
            lons.append(lon)
            lats.append(lat)

        prev_row = temp_row
        prev_col = temp_col

    # prepares final data dictionary to build DataFrame
    out_data = {'ID': [traj_num] * len(prevs), 'PREV': prevs, 'ACT': acts, 'CUR': curs}

    # overwrites the coordinates of the first state in interpolated transitions to be original raw values
    if options['append_coords']:
        lons[0] = row['LON']
        lats[0] = row['LAT']
        out_data['LON'] = lons
        out_data['LAT'] = lats

    # returns final output as DataFrame
    out_df = pd.DataFrame(out_data)
    return out_df


def get_action_interp_reg(row, options, grid_params):
    """Calculates the actions taken from the previous state to reach the current state, interpolating if necessary.

    First, the relative offset between the current and previous state in rows and columns is calculated.
    Then the sign of ``rel_row`` and ``rel_col`` are then used to iteratively describe a sequence of actions
    from the previous state to current state, breaking up state transitions with multiple actions if
    the states are not adjacent (only actions are right, left, up, down, and none). This interpolation
    assumes a deterministic system.

    For example, if ``prev_state = 5``, ``cur_state = 7``, and ``num_cols = 4``, then our state grid is populated
    as follows:

    8  9 10 11
    4  p  6  c
    0  1  2  3

    output: ``pd.DataFrame({})``

    Where p represents the location of the previous state, and c represents the location of the current state.
    Then the current state's position relative to the previous state is ``rel_row = 0``, ``rel_col = 2``. Our action
    spiral then looks like this:

       2            2
    3  0  1  ->  3  p  1  c
       4            4

    output: ``pd.DataFrame({
                            'ID': [traj_num, ],
                            'PREV': [prev_state, ],
                            'ACT': [1, ],
                            'CUR': [prev_state + 1, ]
                            })``

    Because the current state is not adjacent, we interpolate by taking the action that brings us closest to
    the current state: action 1, resulting in a new action spiral and a new previous state.

       2            1
    3  0  1  ->  2  p  c
       4            4

    output: ``pd.DataFrame({
                            'ID': [traj_num] * 2,
                            'PREV': [prev_state, prev_state + 1],
                            'ACT': [1, 1],
                            'CUR': [prev_state + 1, cur_state]
                            })``

    Now, our new previous state is adjacent to the current state, so we can take action 1, which updates our
    previous state to exactly match the current state, so the algorithm terminates and returns the list of
    state-action-state transitions.

    Args:
        row: a pandas Series representing one row of the DataFrame the function is applied to, containing the
        trajectory number, previous state, current state, longitude, and latitude.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        A pandas DataFrame of transitions that interpolate between ``prev_state`` and ``cur_state``. Optionally
        appends the coordinate values of each ``prev_state`` as additional columns.
    """
    # retrieves transition data
    traj_num = int(row['ID'])
    prev_state = int(row['PREV'])
    cur_state = int(row['CUR'])
    num_cols = grid_params['num_cols']

    # instantiate lists to hold column values for final DataFrame output
    prevs = []
    acts = []
    curs = []
    lons = []
    lats = []

    # gets row, column decomposition for previous and current states
    prev_row = prev_state // num_cols
    prev_col = prev_state % num_cols
    cur_row = cur_state // num_cols
    cur_col = cur_state % num_cols
    # calculates current state's position relative to previous state
    rel_row = cur_row - prev_row
    rel_col = cur_col - prev_col

    # write output rows until rel_row and rel_col are both zero
    # out_rows = []
    while not(rel_row == 0 and rel_col == 0):
        # selects action to reduce the largest of rel_row and rel_col
        action = -1
        if rel_row > 0 and rel_col > 0:
            action = 2 if rel_row > rel_col else 1
        elif rel_row > 0 and rel_col == 0:
            action = 2
        elif rel_row > 0 and rel_col < 0:
            action = 2 if rel_row > -rel_col else 3
        elif rel_row == 0 and rel_col > 0:
            action = 1
        elif rel_row == 0 and rel_col < 0:
            action = 3
        elif rel_row < 0 and rel_col > 0:
            action = 4 if -rel_row > rel_col else 1
        elif rel_row < 0 and rel_col == 0:
            action = 4
        elif rel_row < 0 and rel_col < 0:
            action = 4 if -rel_row > -rel_col else 3

        # moves rel_row and rel_col in the opposite directions of their signs
        row_diff = -np.sign(rel_row) if (action == 2 or action == 4) else 0
        col_diff = -np.sign(rel_col) if (action == 1 or action == 3) else 0

        # updates states and relative row, column based on action selected
        rel_row += row_diff
        rel_col += col_diff
        temp_row = prev_row - row_diff
        temp_col = prev_col - col_diff
        temp_state = temp_row * num_cols + temp_col
        prev_state = prev_row * num_cols + prev_col

        # records an interpolated state-action-state transition
        prevs.append(prev_state)
        acts.append(action)
        curs.append(temp_state)

        # gets the coordinates of the interpolated state - will be the coordinates of the middle of the state
        if options['append_coords']:
            lon, lat = state_to_coord(prev_state, options, grid_params)
            lons.append(lon)
            lats.append(lat)

        prev_row = temp_row
        prev_col = temp_col

    # prepares final data dictionary to build DataFrame
    out_data = {'ID': [traj_num] * len(acts), 'PREV': prevs, 'ACT': acts, 'CUR': curs}

    # overwrites the coordinates of the first state in interpolated transitions to be original raw values
    if options['append_coords']:
        lons[0] = row['LON']
        lats[0] = row['LAT']
        out_data['LON'] = lons
        out_data['LAT'] = lats

    # returns final output as DataFrame
    out_df = pd.DataFrame(out_data)
    return out_df


def state_to_coord(state, options, grid_params):
    """Inverse function for ``get_state``.

    Takes in a discretized state, the coordinate precision, and the grid parameters to calculate the coordinates of the
    middle of the state in the grid.

    Args:
        state: The discretized grid square returned by ``get_state``.
        options: The options specified by the user in ``config_file`` on how to run the script.
        grid_params: Specifies the minimum and maximum latitudes and longitudes in the dataset.

    Returns:
        The longitude and latitude representing the middle of the state passed in.
    """
    state_col = state % grid_params['num_cols']
    state_row = state // grid_params['num_cols']
    state_lon = round(grid_params['min_lon'] + grid_params['grid_len'] * (state_col + 0.5), options['prec_coords'])
    state_lat = round(grid_params['min_lat'] + grid_params['grid_len'] * (state_row + 0.5), options['prec_coords'])
    return state_lon, state_lat


if __name__ == '__main__':
    main()
