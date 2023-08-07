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
        print(f"Top {top_n} departments with most {star_unicode} restaurants:\n\n")
    else:
        print(f"{star_unicode} Restaurants in Paris\n")

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
        initial_statement = "Regions with starred restaurants:\n\n"
    else:
        # Check if there is only one region
        if data['region'].nunique() == 1:
            top_n = 1
            paris = True

        # Adjust top_n if it's greater than the number of unique regions
        if top_n > data['region'].nunique():
            top_n = data['region'].nunique()

        initial_statement = f"Top {top_n} regions with most starred restaurants:\n\n"

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

