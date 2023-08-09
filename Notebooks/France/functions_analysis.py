import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np
import math
import re


def plot_high_correlations(df, level='regional', threshold=0.7):
    """
    Plots heatmaps of high correlation matrices either at the regional or departmental level.

    Args:
    - df (DataFrame): The data.
    - level (str): Either 'regional' or 'departmental' to specify the granularity.
    - threshold (float): The absolute correlation value above which to plot. (Default=0.7)

    Returns:
    - None
    """
    if level == 'regional':
        segments = df['region'].unique()
        filter_key = 'region'  # Corrected key for DataFrame filtering
    elif level == 'departmental':
        segments = df['department'].unique()
        filter_key = 'department'  # Corrected key for DataFrame filtering
    else:
        raise ValueError("The 'level' argument must be either 'regional' or 'departmental'.")

    # Calculate grid size based on the number of segments (regions or departments)
    grid_size = math.ceil(math.sqrt(len(segments)))

    # Set up the grid plot layout
    fig, axes = plt.subplots(nrows=grid_size, ncols=grid_size, figsize=(6 * grid_size, 6 * grid_size))

    # Handle different number of segments cases for axes dimensionality
    if len(segments) == 1:
        axes = [[axes]]
    elif len(segments) < 4:
        axes = [axes]

    for index, segment in enumerate(segments):
        # Segment data either by region or by department
        segment_data = df[df[filter_key] == segment]
        corr = segment_data.corr()

        # Mask to filter correlations below the threshold
        mask = (corr < threshold) & (corr > -threshold)

        row, col = divmod(index, grid_size)
        sns.heatmap(corr, annot=True, cmap='coolwarm', vmin=-1, vmax=1, ax=axes[row][col], mask=mask)
        axes[row][col].set_title(f"{segment}")

    # Turn off any remaining unused subplots
    for i in range(index + 1, grid_size * grid_size):
        row, col = divmod(i, grid_size)
        axes[row][col].axis('off')

    plt.tight_layout()
    plt.show()


def print_overview_stats(overview):
    """
    Print the statistics from the overview DataFrame in a formatted table.

    Args:
    - overview (DataFrame): The DataFrame containing the statistics.

    Returns:
    - None
    """

    for region in overview.index:  # Iterating over regions
        print(f"Region: {region}")
        print('-' * len(f"Region: {region}"))

        # Prepare the headers
        headers = ["Statistic"] + [col.capitalize() for col in overview.columns.levels[0]]
        table_data = []

        # Collecting stats data for each column in the same order as headers
        for stat in overview.columns.levels[1]:  # Iterating over statistics (e.g., 'mean', 'std')
            row_data = [stat.capitalize()]
            for col in overview.columns.levels[0]:  # Iterating over main columns (e.g., 'poverty_rate')
                value = overview.at[region, (col, stat)]
                row_data.append(f"{value:.2f}")
            table_data.append(row_data)

        # Get the max widths for each column for formatting
        col_widths = [
            max(len(str(word)) for word in col)
            for col in zip(*table_data, headers)
        ]

        # Print the headers
        header_row = " | ".join(str(header).ljust(col_widths[i]) for i, header in enumerate(headers))
        print(header_row)
        print('-' * len(header_row))

        # Print each row
        for row in table_data:
            print(" | ".join(str(item).ljust(col_widths[i]) for i, item in enumerate(row)))

        print("=" * len(header_row))  # Print a separator between regions


def find_extreme_departments(df, column_names):
    """
    Find the department with the maximum and minimum value of the given columns for each region.

    Args:
    - df (DataFrame): The DataFrame containing the data.
    - column_names (Union[str, List[str]]): The name or names of the columns to check.

    Returns:
    - results (dict): A dictionary containing the max and min DataFrames for each column.
    """

    # Check if column_names is a string; if so, convert to a list
    if isinstance(column_names, str):
        column_names = [column_names]

    results = {}

    # Define a dictionary mapping column names to units
    units_map = {
        'poverty_rate': '%',
        'unemployment_rate': '%',
        'net_wage': '€',
        'population': 'people',
        'pop_density': 'inhabitants/sq_km',
        # Add other columns and their units as required
    }

    grouped_regions = df['region'].unique()

    for region in grouped_regions:
        print(f"Region: {region}\n")

        for column_name in column_names:
            max_idx = df[df['region'] == region][column_name].idxmax()
            min_idx = df[df['region'] == region][column_name].idxmin()

            max_row = df.loc[max_idx]
            min_row = df.loc[min_idx]

            unit = units_map.get(column_name, '')  # Get the unit; if not found, default to empty string

            print(column_name)
            print(f"\tMax: {max_row['department']} ({max_row['code']}) = {max_row[column_name]} {unit}")
            print(f"\tMin: {min_row['department']} ({min_row['code']}) = {min_row[column_name]} {unit}\n")

            results[column_name] = {
                'max': max_row,
                'min': min_row
            }
        print("-" * 50)  # Print a separator between regions


def plot_side_by_side(df, cols_of_interest, french_means):
    """
    Plot side-by-side bar charts for each column in cols_of_interest.

    Args:
    - df (DataFrame): Dataframe containing the data.
    - cols_of_interest (list of str): List of column names to plot.
    - french_means (dict): Dictionary mapping from metric names to their corresponding French means.

    Returns:
    - None. Displays the plots.

    The function will display a 2xN layout for the plots based on the number of
    cols_of_interest. If a matching French mean is found for a given column, it will be displayed as
    a horizontal dashed line on the corresponding plot.
    """

    n_metrics = len(cols_of_interest)

    # Layout is always 2xN
    nrows = 2
    ncols = math.ceil(n_metrics / 2)

    fig, axes = plt.subplots(nrows, ncols, figsize=(6 * ncols, 5 * nrows))

    # Adjust axes to be 2D for consistent indexing
    if n_metrics == 1:
        axes = np.array([[axes]])
    elif n_metrics == 2:
        axes = np.array([axes])

    for idx, col in enumerate(cols_of_interest):
        ax = axes[idx // ncols][idx % ncols]

        # Determine unit based on the column name
        if re.search(r"unemployment|poverty", col, re.IGNORECASE):
            unit = "%"
        elif re.search(r"wage|salary|net_wage", col, re.IGNORECASE):
            unit = "€"
        else:
            unit = ""

        # Locate corresponding French mean if available
        matching_mean_key = next((key for key in french_means.keys() if re.search(key, col, re.IGNORECASE)), None)
        matching_mean = french_means.get(matching_mean_key) if matching_mean_key else None

        if matching_mean:
            ax.axhline(y=matching_mean, color='r', linestyle='--', label='French Avg')

        # Plot data
        ax.bar(df['code'], df[col], label='Department Value')

        # Set y-axis limits for a better representation
        data_min = df[col].min()
        data_max = df[col].max()
        buffer = (data_max - data_min) * 0.10  # 10% of the range as buffer
        ax.set_ylim(data_min - buffer, data_max + buffer)

        # Set title, labels, and ticks
        ax.set_title(col.replace("_", " "))
        ax.set_xlabel('Department Code')
        ax.set_ylabel(f"{col} {unit}")
        ax.set_xticks(df['code'])
        ax.legend()

    plt.tight_layout()
    plt.show()





