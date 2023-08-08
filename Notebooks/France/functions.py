import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
import pyproj
import seaborn as sns
import geopandas as gpd
import mapclassify
import folium
import branca


def dataframe_info(data):
    print(f"Unique Departments: {data['department'].nunique()}")
    print(f"Unique Cities: {data['city'].nunique()}")
    print(f"Shape: {data.shape}")
    display(data.head(3))


def top_restaurants_by_department(data, star_rating, top_n, display_restaurants=True, display_info=False):
    """
    Print the top_n departments with the highest count of 'star_rating' restaurants.
    Also prints the names and cities of those restaurants if display_restaurants is True.
    Additionally, displays the cuisine and URL of each restaurant if display_info is True.

    Args:
        data (pandas.DataFrame): The dataset containing restaurant info.
        star_rating (int): The Michelin star rating (1, 2, or 3).
        top_n (int): The number of top departments to consider.
        display_restaurants (bool): Whether to display individual restaurants. Default is True.
        display_info (bool): prints style of cuisine and URL. Default is False.
    """
    paris = False

    # Filter out 'bib_gourmands' before the analysis
    data = data[data['stars'].isin([1.0, 2.0, 3.0])]

    # Check if there is only one department
    if data['department'].nunique() == 1:
        top_n = 1
        paris = True

    # Change star_rating to float
    star_rating = float(star_rating)
    star_unicode = int(star_rating) * u'\u2B50'

    # Filter by 'star_rating'
    filtered_data = data[data['stars'] == star_rating]

    # Sort filtered_data by 'department'
    sorted_filtered_data = filtered_data.sort_values(by=['department'])

    # Group by 'department_num' and get top_n
    top_depts = sorted_filtered_data['department_num'].value_counts().nlargest(top_n)

    if not paris:
        print(f"Top {top_n} departments with most {star_unicode} restaurants:\n\n\n")
    else:
        print(f"{star_unicode} Restaurants in Paris\n\n\n")

    # Print the names of the restaurants, the towns they are in and the department number
    for dept_num, restaurant_count in top_depts.iteritems():
        restaurants = sorted_filtered_data[sorted_filtered_data['department_num'] == dept_num][['name', 'city',
                                                                    'department', 'department_num', 'cuisine', 'url']]
        dept = restaurants['department'].values[0]  # Get the department name from the first restaurant
        restaurant_word = "Restaurant" if restaurant_count == 1 else "Restaurants"
        print(f"Department: {dept} ({dept_num})\n{restaurant_count} {star_unicode} {restaurant_word}\n")

        if display_restaurants:
            for index, row in restaurants.iterrows():
                print(f"Restaurant: {row['name']}\nLocation: {row['city']}")
                if display_info:
                    print(f"Style of Cuisine: {row['cuisine']}\nURL: {row['url']}\n")
        print("\n")


def top_restaurants_by_region(data, star_rating, top_n, display_restaurants=True, display_info=False):
    """
    Print the top_n regions with the highest count of 'star_rating' restaurants.
    Also prints the names and cities of those restaurants if display_restaurants is True.

    Args:
        data (pandas.DataFrame): The dataset containing restaurant info.
        star_rating (str or int): The Michelin star rating (1, 2, 3, or 'all').
        top_n (str or int): The number of top regions to consider or 'all' for all regions.
        display_restaurants (bool): Whether to display individual restaurants. Default is True.
        display_info (bool): Whether to display additional restaurant info (cuisine and url). Default is False.
    """
    paris = False

    # Filter out 'bib_gourmands' before the analysis
    data = data[data['stars'].isin([1.0, 2.0, 3.0])]

    # If 'all' is passed, use all unique regions, else use top_n
    if isinstance(top_n, str) and top_n.lower() == 'all':
        top_n = data['region'].nunique()
        initial_statement = "Regions with starred restaurants:\n\n\n"
    else:
        # Check if there is only one region
        if data['region'].nunique() == 1:
            top_n = 1
            paris = True

        # Adjust top_n if it's greater than the number of unique regions
        if top_n > data['region'].nunique():
            top_n = data['region'].nunique()

        initial_statement = f"Top {top_n} regions with most starred restaurants:\n\n\n"

    # Group by 'region' and get top_n
    top_regions = data['region'].value_counts().nlargest(top_n)

    if not paris:
        print(initial_statement)
    else:
        print("Restaurants in Paris\n")

    # Print the names of the restaurants, the towns they are in and the region number
    for region, restaurant_count in top_regions.iteritems():
        restaurants_region = data[data['region'] == region]

        print(f"Region: {region}\n{restaurant_count} Starred Restaurants\n")

        if star_rating == 'all':
            star_ratings = [3, 2, 1]
        else:
            star_ratings = [star_rating]

        for star in star_ratings:
            star_unicode = int(star) * u'\u2B50'
            restaurants = restaurants_region[restaurants_region['stars'] == star][['name', 'city', 'cuisine', 'url']]
            restaurant_count_star = len(restaurants)

            if restaurant_count_star > 0:
                restaurant_word = "Restaurant" if restaurant_count_star == 1 else "Restaurants"
                print(f"{restaurant_count_star} {star_unicode} {restaurant_word}\n")

                if display_restaurants:
                    for index, row in restaurants.iterrows():
                        print(f"Restaurant: {row['name']}\nLocation: {row['city']}")
                        if display_info:
                            print(f"Cuisine: {row['cuisine']}\nURL: {row['url']}\n")
        print("\n")


def plot_choropleth(df, column, title, regional=False, restaurants=False, cmap='Blues', figsize=(10, 10)):
    """
    Function to plot a choropleth map and optionally restaurant locations.

    Args:
        df (GeoDataFrame): The DataFrame containing the data.
        column (str): The name of the column to plot.
        title (str): The title of the plot.
        regional (bool): If True, show regional map with departments labeled. Default is False.
        restaurants (bool): If True, plot restaurant locations. Default is False.
        cmap (str): The colormap to use. Default is 'Blues'.
        figsize (tuple): The size of the figure. Default is (10, 10).

    Returns:
        None
    """
    fig, ax = plt.subplots(1, 1, figsize=figsize)
    df = df.to_crs("EPSG:2154")  # RGF93 / Lambert-93

    df.plot(column=column, cmap=cmap, linewidth=0.8, ax=ax, edgecolor='0.8', legend=True,
            legend_kwds={'orientation': "horizontal"})

    all_handles = []
    all_labels = []

    if regional:
        region_name = df['region'].unique()[0]
        plt.title(f"{title}\n{region_name}")

        for x, y, label in zip(df.geometry.centroid.x, df.geometry.centroid.y, df['code']):
            ax.text(x, y, label, fontsize=8, backgroundcolor='white')

        dept_handles = [Line2D([0], [0], marker='o', color='w',
                               label=f"{code}: {df.loc[df['code'] == code, 'department'].values[0]}", markersize=0,
                               alpha=0) for code in df['code'].unique()]
        all_handles.extend(dept_handles)
        all_labels.extend([h.get_label() for h in dept_handles])

    if restaurants:
        star_colors = {'1': 'green', '2': 'orange', '3': 'red'}
        added_labels = set()

        transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)

        for star, color in star_colors.items():
            for row in df.iterrows():
                locations = row[1]['locations']
                if locations[star]:  # Check if it's not None
                    for lat, long in locations[star]:
                        x, y = transformer.transform(long, lat)
                        ax.scatter(x, y, c=color, s=50, marker='d')

            if star not in added_labels:
                all_handles.append(
                    Line2D([0], [0], marker='d', color=color, label=f"{star} star restaurant",
                           markersize=8, linestyle='None'))
                added_labels.add(star)

        all_labels.extend([h.get_label() for h in all_handles[len(all_handles) - len(star_colors):]])

    if regional or restaurants:
        ax.legend(handles=all_handles, labels=all_labels, loc='upper left', bbox_to_anchor=(1, 1), title="Legend")
    else:
        plt.suptitle(title)

    ax.set_yticklabels([])
    ax.set_xticklabels([])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.axis('off')

    plt.tight_layout()
    plt.show()


def plot_multi_choropleth(df, columns, titles, main_title=None, cmap='Blues', figsize=(10, 10)):
    """
    Function to plot a choropleth map.

    Args:
        df (GeoDataFrame): The DataFrame containing the data.
        columns (list): The names of the columns to plot.
        titles (list): The titles of the plots.
        cmap (str): The colormap to use. Default is 'Blues'.
        figsize (tuple): The size of the figure. Default is (10, 10).

    Returns:
        None
    """
    # Ensure that `columns` and `titles` are lists
    if not isinstance(columns, list):
        columns = [columns]

    if not isinstance(titles, list):
        titles = [titles]

    # Define the number of cols for subplots
    cols = len(columns)

    fig, axes = plt.subplots(1, cols, figsize=figsize)

    # In case there's only one subplot, convert axes to list
    if cols == 1:
        axes = [axes]

    for ax, column, title in zip(axes, columns, titles):
        df.plot(column=column,
                cmap=cmap,
                linewidth=0.8,
                ax=ax,
                edgecolor='0.8',
                legend=True,
                legend_kwds={'orientation': "horizontal"})
        ax.set_title(title)
        # Check if legend exists
        legend = ax.get_legend()
        if legend:
            # Remove legend title
            legend.set_title('')

        ax.set_yticklabels([])
        ax.set_xticklabels([])
        ax.set_xticks([])
        ax.set_yticks([])
        ax.axis('off')

    # Set global title
    if main_title:
        fig.suptitle(main_title, fontsize=16)

    # Adjust the subplot parameters for better spacing and accommodate suptitle
    fig.tight_layout(rect=[0, 0, 1, 0.96])
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

    population = round(dept_info['municipal_population'], -3)  # round to nearest 1000
    pop_density = dept_info['population_density(inhabitants/sq_km)']
    area = dept_info['area(sq_km)']
    poverty = dept_info['poverty_rate(%)']
    unemployment = dept_info['average_annual_unemployment_rate(%)']
    hourly_wage = dept_info['average_net_hourly_wage(€)']

    print(f"Demographics of {dept_info['department']} ({dept_info['code']}):\n")
    print(f"Population: {population}")
    print(f"Population Density: {pop_density:.2f} people/sq. km")
    print(f"Area: {area:.2f} sq. km")
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
                          f"\nLocation: {restaurant['city']}"
                          f"\nStyle of Cuisine: {restaurant['cuisine']}"
                          f"\nURL: {restaurant['url']}"
                          f"\nPrice: {restaurant['price']}\n")
            print("")

