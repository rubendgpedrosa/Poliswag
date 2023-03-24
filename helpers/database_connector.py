import pymysql

import helpers.constants as constants

def get_data_from_database(query, database = "rocketdb"):
    connection = pymysql.connect(host=constants.DB_IP,
        user=constants.DB_USER,
        password=constants.DB_PASSWORD,
        db=database
    )
    
    # Prepare cursor and execute query
    cursor = connection.cursor()
    cursor.execute(query)

    # Fetch results
    results = cursor.fetchall()

    # Get column names
    columns = [col[0] for col in cursor.description]

    # Close the connection
    connection.close()

    return [{'columns': columns, 'data': row} for row in results]

def execute_query_to_database(query, database = "poliswag"):
    connection = pymysql.connect(host=constants.DB_IP,
        user=constants.DB_USER,
        password=constants.DB_PASSWORD,
        db=database
    )

    # Prepare cursor and execute query
    cursor = connection.cursor()
    cursor.execute(query)

    # Get the number of affected rows
    affected_rows = cursor.rowcount

    # Commit the transaction and close the connection
    connection.commit()
    connection.close()

    return affected_rows
