import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import numpy as np
import math
import re
import math
from IPython.core.display import display, HTML


def plot_high_correlations(df, level='regional', threshold=0.7):
    """
    Plots heatmaps of high correlation matrices either at the regional or departmental level.
    Returns a dictionary of correlation matrices above the threshold.

    Args:
    - df (DataFrame): The data.
    - level (str): Either 'regional' or 'departmental' to specify the granularity.
    - threshold (float): The absolute correlation value above which to plot. (Default=0.7)

    Returns:
    - high_corrs (dict): A dictionary where keys are segments and values are correlation matrices with values above threshold.
    """
    if level == 'regional':
        segments = df['region'].unique()
        filter_key = 'region'
    elif level == 'departmental':
        segments = df['department'].unique()
        filter_key = 'department'
    else:
        raise ValueError("The 'level' argument must be either 'regional' or 'departmental'.")

    # Calculate grid size based on the number of segments
    grid_size = math.ceil(math.sqrt(len(segments)))

    # Set up the grid plot layout
    fig, axes = plt.subplots(nrows=grid_size, ncols=grid_size, figsize=(6 * grid_size, 6 * grid_size))

    if len(segments) == 1:
        axes = [[axes]]
    elif len(segments) < 4:
        axes = [axes]

    high_corrs = {}  # Dictionary to store high correlation matrices for each segment

    for index, segment in enumerate(segments):
        segment_data = df[df[filter_key] == segment]
        corr = segment_data.corr()

        # Mask for upper triangle and low correlations
        mask = np.triu(np.ones_like(corr, dtype=bool)) | (corr.abs() < threshold)

        high_corr = corr[~mask].dropna(how='all').T.dropna(how='all')  # filter NaN rows and columns

        # Add to the dictionary
        high_corrs[segment] = high_corr

        row, col = divmod(index, grid_size)
        sns.heatmap(corr, annot=True, cmap='coolwarm', vmin=-1, vmax=1, ax=axes[row][col], mask=mask)
        axes[row][col].set_title(f"{segment}")

    for i in range(index + 1, grid_size * grid_size):
        row, col = divmod(i, grid_size)
        axes[row][col].axis('off')

    plt.tight_layout()
    plt.show()

    return high_corrs


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


def plot_violinplots(data, columns, x_col='region'):
    """
    Generate violin plots for a list of columns against a categorical column.

    Args:
    - data (DataFrame): The source data.
    - columns (list): List of columns for which to create violin plots.
    - x_col (str): The categorical column to plot against (default: 'region').

    Returns:
    - None
    """
    # Set the style
    sns.set_style("whitegrid")

    # Define a dictionary mapping column names to units
    units_map = {
        'poverty_rate': '%',
        'unemployment_rate': '%',
        'net_wage': '€',
        'per_capita_GDP': '€',
        'population': 'people',
        'pop_density': 'inhabitants/sq_km',
    }

    for col in columns:
        plt.figure(figsize=(12, 6))

        # Creating a temporary DataFrame to capitalize elements of the x_col
        temp_data = data.copy()
        temp_data[x_col] = temp_data[x_col].str.title()  # Use title() to capitalize each word

        sns.violinplot(data=temp_data, x=x_col, y=col, palette='pastel', inner="quartile")

        # Constructing the y-label using the units_map
        words = col.split('_')
        ylabel = ""

        for word in words:
            if "gdp" in word.lower():
                ylabel += "GDP"
            else:
                ylabel += word.capitalize()
            ylabel += ' '

        if col in units_map:
            ylabel += f'({units_map[col]})'

        # Removing the trailing space
        ylabel = ylabel.strip()

        # Adding title and labels
        plt.title(f'Distribution of {ylabel} by Region', fontsize=16)
        plt.xlabel(x_col.capitalize(), fontsize=14)
        plt.ylabel(ylabel, fontsize=14)
        plt.xticks()  # Rotate x_col values for clarity if needed

        # Display the plot
        plt.tight_layout()
        plt.show()


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
        'per_capita_GDP': '€',
        'population': 'people',
        'pop_density': 'inhabitants/sq_km'
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


def get_random_restaurants(df, star_rating=None, seed=42):
    """
    Select random restaurants based on their Michelin star and price ratings.

    Parameters:
    - df (pd.DataFrame): The DataFrame containing restaurant information.
    - star_rating (int, optional): The desired Michelin star rating to filter by (1, 2, 3). Default is None, which includes all star ratings.
    - seed (int, optional): Seed for random number generation for reproducibility. Default is 42.

    Returns:
    - dict: A dictionary with keys being the price ratings and values being lists of randomly selected restaurants.
    """
    # Set random seed for reproducibility
    np.random.seed(seed)

    # Filter for starred restaurants only
    starred_restos = df[df['stars'] > 0.5]

    # If a specific star rating is provided, filter by it
    if star_rating:
        starred_restos = starred_restos[starred_restos['stars'] == star_rating]

    # Price categories
    price_categories = ['€€€€', '€€€']

    # Dictionary to store selections
    selections = {}

    for price in price_categories:
        subset = starred_restos[starred_restos['price'] == price]

        # Continue to the next price category if there are no restaurants available
        if subset.empty:
            continue

        sample = subset.sample(min(5, len(subset)))  # In case there are less than 5 restaurants for a category
        selections[price] = [(row['name'], row['location'], row['stars'], row['url']) for _, row in sample.iterrows()]

    return selections


def display_restaurants(selections):
    # Display the selected restaurants
    for price, restaurants in selections.items():
        print(f"Price rating: {price}")
        for name, location, stars, url in restaurants:
            star_unicode = int(stars) * u'\u2B50'
            html_content = f"<a href='{url}' target='_blank'>{name}</a> ({star_unicode}) - {location}"
            display(HTML(html_content))
        print("\n")


def plot_side_by_side(df, cols_of_interest, french_means, granularity='department'):
    """
    Plot side-by-side bar charts for each column in cols_of_interest.

    Args:
    - df (DataFrame): Dataframe containing the data.
    - cols_of_interest (list of str): List of column names to plot.
    - french_means (dict): Dictionary mapping from metric names to their corresponding French means.
    - granularity (str): Level of granularity - 'arrondissement', or 'department'. Default is 'department'.

    Returns:
    - None. Displays the plots.

    The function will display a 2xN layout for the plots based on the number of
    cols_of_interest. If a matching French mean is found for a given column, it will be displayed as
    a horizontal dashed line on the corresponding plot.
    """
    # Set the label column based on granularity
    label_column = 'code' if granularity == 'department' else 'arrondissement'

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
        elif re.search(r"wage|salary|net_wage|GDP", col, re.IGNORECASE):
            unit = "€"
        else:
            unit = ""

        # Locate corresponding French mean if available
        matching_mean_key = next((key for key in french_means.keys() if re.search(key, col, re.IGNORECASE)), None)
        matching_mean = french_means.get(matching_mean_key) if matching_mean_key else None

        if matching_mean:
            ax.axhline(y=matching_mean, color='r', linestyle='--', label='French Avg')

        # Plot data
        ax.bar(df[label_column], df[col], label='Value')

        # Set y-axis limits for a better representation
        data_min = df[col].min()
        data_max = df[col].max()
        buffer = (data_max - data_min) * 0.10  # 10% of the range as buffer
        ax.set_ylim(data_min - buffer, data_max + buffer)

        # Set title, labels, and ticks
        ax.set_title(col.replace("_", " "))
        ax.set_xlabel('Department Code' if granularity == 'department' else 'Arrondissement')
        ax.set_ylabel(f"{col} {unit}")
        ax.set_xticks(df[label_column])
        ax.legend()

    plt.tight_layout()
    plt.show()


