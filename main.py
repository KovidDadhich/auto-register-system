from config.settings import URL
from backend.scraper.gcms_scraper import GCMSScraper
from scripts.create_database import create_DB
from scripts.insert_test_case import insertTestCase

def run_scraper():
    scraper = GCMSScraper()
    scraper.initialize_session()

    data = scraper.fetch_case("2009/7852")

    print(data)


def main():
    # create_DB()

    # # insert-test-case
    # insertTestCase()

    print(f"Starting Register System for: {URL}")
    # Call your scraper function
    run_scraper()

if __name__ == "__main__":
    main()