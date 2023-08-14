import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from fuzzywuzzy import process
import pyproj
import seaborn as sns
import geopandas as gpd
import mapclassify
import folium
import branca


def dataframe_info(data):
    # Check if DataFrame or GeoDataFrame
    if isinstance(data, gpd.GeoDataFrame):
        print("GeoDataFrame.")
    else:
        pass
    # For each column of interest, check if it exists before printing its stats
    columns_of_interest = ["region", "department", "arrondissement", "location"]
    for col in columns_of_interest:
        if col in data.columns:
            print(f"Unique {col.capitalize()}s: {data[col].nunique()}")
        else:
            pass
    print(f"\nShape: {data.shape}")
    display(data.head(3))


def _get_best_match(search_str, df, column):
    """
    Returns the best matching string from unique values of a specified column in a dataframe.

    Args:
    - search_str (str): The string to search for.
    - df (pandas.DataFrame): DataFrame containing the data.
    - column (str): The column name in which to search for the string.

    Returns:
    - str or None: The best matching string if a good match is found, otherwise None.

    Example:
    --------
    best_region = get_best_match("Provence", all_france, "region")
    """
    unique_choices = df[column].unique()
    best_match, score = process.extractOne(search_str, unique_choices)

    if score > 80:  # adjust this threshold
        return best_match
    else:
        return None


def filter_dataframe(data, regions=None, departments=None, exclude_regions=None, exclude_departments=None):
    """
    Filters the dataframe based on given conditions.

    Args:
    - data (pandas.DataFrame): The main dataframe.
    - regions (list[str]/str, optional): Region(s) to focus on.
    - departments (list[str]/str, optional): Department(s) to focus on.
    - exclude_regions (list[str]/str, optional): Region(s) to exclude.
    - exclude_departments (list[str]/str, optional): Department(s) to exclude.

    Returns:
    - pandas.DataFrame: Filtered dataframe.
    """
    # Convert single strings to lists for uniform handling
    regions = [regions] if isinstance(regions, str) else regions
    departments = [departments] if isinstance(departments, str) else departments
    exclude_regions = [exclude_regions] if isinstance(exclude_regions, str) else exclude_regions
    exclude_departments = [exclude_departments] if isinstance(exclude_departments, str) else exclude_departments

    if regions:
        matched_regions = [_get_best_match(region, data, 'region') for region in regions]
        data = data[data['region'].isin(matched_regions)]

    if departments:
        matched_departments = [_get_best_match(department, data, 'department') for department in departments]
        data = data[data['department'].isin(matched_departments)]

    if exclude_regions:
        matched_exclude_regions = [_get_best_match(region, data, 'region') for region in exclude_regions]
        data = data[~data['region'].isin(matched_exclude_regions)]

    if exclude_departments:
        matched_exclude_departments = [_get_best_match(department, data, 'department') for department in
                                       exclude_departments]
        data = data[~data['department'].isin(matched_exclude_departments)]

    return data


def top_restaurants(data, granularity, star_rating, top_n, display_restaurants=True, display_info=False):
    """
    Display top_n restaurants with the highest count of 'star_rating' restaurants
    based on different levels of granularity: region, department, or arrondissement.

    Also prints the names and cities of those restaurants if display_restaurants is True.
    Additionally, displays the cuisine and URL of each restaurant if display_info is True.

    Args:
        data (pandas.DataFrame): The dataset containing restaurant info.
        granularity (str): One of 'region', 'department', 'arrondissement'.
        star_rating (int): The Michelin star rating (1, 2, or 3).
        top_n (int): The number of top (granularity) to consider.
        display_restaurants (bool): Whether to display individual restaurants. Default is True.
        display_info (bool): prints style of cuisine and URL. Default is False.
    """
    # Filter out 'bib_gourmands' before the analysis
    data = data[data['stars'].isin([1.0, 2.0, 3.0])]

    # Convert star_rating to its corresponding unicode representation
    star_unicode = int(star_rating) * u'\u2B50'

    # Filter only rows matching the given star rating
    filtered_data = data[data['stars'] == star_rating]

    # Determine the unique values in the granularity column
    unique_values = filtered_data[granularity].unique()

    # Sort filtered_data by granularity
    sorted_filtered_data = filtered_data.sort_values(by=[granularity])

    # Group by granularity and get top_n
    top_areas = sorted_filtered_data[granularity].value_counts().nlargest(top_n)

    # If there's only one unique value, you're dealing with a specific region, department, or arrondissement.
    if len(unique_values) == 1:
        print(f"{star_unicode} restaurants in {unique_values[0]}\n")
    else:
        print(f"Top {top_n} {granularity}s with most {star_unicode} restaurants:\n")

    # Displaying the top restaurants or areas
    for area, restaurant_count in top_areas.iteritems():
        restaurants_in_area = sorted_filtered_data[sorted_filtered_data[granularity] == area][
            ['name', 'location', granularity, 'cuisine', 'url']]

        restaurant_word = "Restaurant" if restaurant_count == 1 else "Restaurants"
        print(f"{granularity.capitalize()}: {area}\n{restaurant_count} {star_unicode} {restaurant_word}\n")

        if display_restaurants:
            for index, row in restaurants_in_area.iterrows():
                print(f"Restaurant: {row['name']}\nLocation: {row['location']}")
                if display_info:
                    print(f"Style of Cuisine: {row['cuisine']}\nURL: {row['url']}\n")
        print("\n")


def plot_choropleth(df, column, title, granularity='department', restaurants=False,
                    show_legend=True, cmap='Blues', figsize=(10, 10)):
    """
    Function to plot a choropleth map and optionally restaurant locations.

    Args:
        df (GeoDataFrame): The DataFrame containing the data.
        column (str): The name of the column to plot.
        title (str): The title of the plot.
        granularity (str): Level of granularity - 'arrondissement', 'department', or 'region'. Default is 'department'.
        restaurants (bool): If True, plot restaurant locations. Default is False.
        show_legend (bool): If True, display the legend. Default is True.
        cmap (str): The colormap to use. Default is 'Blues'.
        figsize (tuple): The size of the figure. Default is (10, 10).

    Returns:
        None
    """
    # Check if the column exists and contains numerical data
    if column not in df.columns:
        raise ValueError(
            f"The column '{column}' does not exist in the DataFrame. "
            f"Available numerical columns are:\n{df.select_dtypes(include=['number']).columns.tolist()}")

    if not np.issubdtype(df[column].dtype, np.number):
        raise ValueError(
            f"The column '{column}' does not contain numerical data. "
            f"Available numerical columns are:\n{df.select_dtypes(include=['number']).columns.tolist()}")

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    df = df.to_crs("EPSG:2154")  # RGF93 / Lambert-93

    # Plot the choropleth and get the returned artists and collection
    cax = df.plot(column=column, cmap=cmap, linewidth=0.8, edgecolor='0.8', legend=False, ax=ax)

    # Create the colorbar at the bottom
    cbar = fig.colorbar(cax.collections[0], ax=ax, orientation='horizontal', fraction=0.05, pad=0.05)
    cbar.set_label(column)

    all_handles = []
    all_labels = []

    # Handle the label_column based on granularity
    if granularity == 'region':
        label_column = 'region'
    elif granularity in ['department', 'arrondissement']:
        label_column = 'code'
    else:
        raise ValueError(f"Invalid granularity: {granularity}. Choose from ['region', 'department', 'arrondissement'].")

    unique_regions = df['region'].unique()

    if len(unique_regions) == 1:
        title += f"\n{unique_regions[0]}"

    for x, y, label in zip(df.geometry.centroid.x, df.geometry.centroid.y, df[label_column]):
        ax.text(x, y, label, fontsize=8, backgroundcolor='white')

    if show_legend:
        label_handles = [Line2D([0], [0], marker='o', color='w',
                                label=f"{code}: {df.loc[df[label_column] == code, granularity].values[0]}", markersize=0,
                                alpha=0) for code in df[label_column].unique()]
        all_handles.extend(label_handles)
        all_labels.extend([h.get_label() for h in label_handles])

    if restaurants:
        star_colors = {'1': 'green', '2': 'orange', '3': 'red'}
        added_labels = set()

        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)

        for star, color in star_colors.items():
            for row in df.iterrows():
                locations = row[1]['locations']
                if locations[str(star)]:  # Check if it's not None
                    for lat, long in locations[str(star)]:
                        x, y = transformer.transform(long, lat)
                        ax.scatter(x, y, c=color, s=50, marker='d')

            if star not in added_labels:
                all_handles.append(
                    Line2D([0], [0], marker='d', color=color, label=f"{star} star restaurant",
                           markersize=8, linestyle='None'))
                added_labels.add(star)

        all_labels.extend([h.get_label() for h in all_handles[len(all_handles) - len(star_colors):]])

    # Move the legend to the left
    if show_legend and (granularity in ['region', 'department', 'arrondissement'] or restaurants):
        ax.legend(handles=all_handles, labels=all_labels, loc='upper left', bbox_to_anchor=(-0.2, 1), title="Legend")

    plt.suptitle(title)
    ax.set_yticklabels([])
    ax.set_xticklabels([])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.axis('off')

    plt.tight_layout()
    plt.show()


def plot_department(geo_df, data_df, department_code,
                    display_restaurants=True, display_info=False, figsize=(10, 10)):
    """
    Plot a department map with starred restaurants and display a list of those restaurants.

    Args:
        geo_df (GeoDataFrame): The GeoDataFrame containing department boundaries.
        data_df (pandas.DataFrame): The standard dataframe with restaurant data.
        department_code (str): The code for the department of interest.
        display_restaurants (bool): If True, display individual restaurants on the map.
        display_info (bool): If True, print additional info about restaurants.
        figsize (tuple): Size of the figure to plot. Default (10,10)
    """
    # Filter out 0.5 star restaurants
    data_df = data_df[data_df['stars'] != 0.5]

    # Filter both dataframes by department_code
    dept_geo = geo_df[geo_df['code'] == department_code]
    dept_data = data_df[data_df['department_num'] == department_code]

    fig, ax = plt.subplots(figsize=figsize)
    dept_geo.plot(ax=ax, color='lightgrey', edgecolor='k')

    if display_restaurants:
        star_colors = {'1': 'green', '2': 'orange', '3': 'red'}

        added_stars = set()
        for star, color in star_colors.items():
            star_data = dept_data[dept_data['stars'] == float(star)]
            for _, row in star_data.iterrows():
                ax.scatter(row['longitude'], row['latitude'], c=color, s=50, marker='d',
                           label=f"{star} star" if star not in added_stars else "")
                added_stars.add(star)

    handles, labels = ax.get_legend_handles_labels()
    unique_labels = dict(zip(labels, handles))
    ax.legend(unique_labels.values(), unique_labels.keys())

    ax.set_title(f"Michelin Starred Restaurants\n{dept_geo['department'].values[0]}")
    ax.set_axis_off()
    plt.show()

    # 2. Demographics:
    dept_info = dept_geo.iloc[0]  # get the information of the department from the GeoDataFrame

    capital = dept_info['capital']
    population = dept_info['municipal_population']  # round to nearest 1000
    pop_density = dept_info['population_density(inhabitants/sq_km)']
    area = dept_info['area(sq_km)']
    gdp = dept_info['GDP_per_capita(€)']
    poverty = dept_info['poverty_rate(%)']
    unemployment = dept_info['average_annual_unemployment_rate(%)']
    hourly_wage = dept_info['average_net_hourly_wage(€)']

    print(f"Demographics of {dept_info['department']} ({dept_info['code']}):\n")
    print(f"Capital: {capital}")
    print(f"Population: {population}")
    print(f"Population Density: {pop_density:.2f} people/sq. km")
    print(f"Area: {area:.2f} sq. km")
    print(f"Per Capita GDP: {gdp:.2f} €")
    print(f"Poverty Rate: {poverty} %")
    print(f"Unemployment Rate: {unemployment} %")
    print(f"Mean Hourly Wage: {hourly_wage} €\n\n\n")

    # 3. List of Starred Restaurants:
    star_columns = ['3_star', '2_star', '1_star']

    for star_col in star_columns:
        # Extract the star count and determine the appropriate Unicode star symbol
        star_count = dept_geo[star_col].values[0]
        if star_count > 0:  # Only proceed if there are restaurants with that star rating
            star_rating = int(star_col.split('_')[0])
            star_unicode = star_rating * u'\u2B50'

            restaurant_word = "Restaurant" if star_count == 1 else "Restaurants"
            print(f"{star_count} {star_unicode} {restaurant_word}:\n")

            # Get the restaurants from the data dataframe with the matching star rating
            restaurants_in_dept = dept_data[dept_data['stars'] == star_rating]

            for _, restaurant in restaurants_in_dept.iterrows():
                if display_info:
                    print(f"Restaurant: {restaurant['name']}"
                          f"\nAddress: {restaurant['address']}"
                          f"\nLocation: {restaurant['location']}"
                          f"\nStyle of Cuisine: {restaurant['cuisine']}"
                          f"\nURL: {restaurant['url']}"
                          f"\nPrice: {restaurant['price']}\n")
            print("")

