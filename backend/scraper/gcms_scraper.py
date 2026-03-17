import requests
import re
from bs4 import BeautifulSoup
from datetime import date
import time

from config.settings import URL

from backend.logger import get_logger, log_scrape_event
logger = get_logger(__name__)


class GCMSScraper:

    # URL = "https://gcms.rajasthan.gov.in/gcmsportal/BorCaseSearch.aspx"

    HEADERS = {
        "X-MicrosoftAjax": "Delta=true",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": URL,
        "Origin": "https://gcms.rajasthan.gov.in",
        "User-Agent": "Mozilla/5.0"
    }

    def __init__(self):
        self.session = requests.Session()
        self.viewstate = None
        self.generator = None
        self.csrf = None

    def initialize_session(self):
        try:
            res = self.session.get(URL, timeout=40)
            self.viewstate = self.extract_hidden(res.text, "__VIEWSTATE")
            self.generator = self.extract_hidden(res.text, "__VIEWSTATEGENERATOR")
            self.csrf = self.extract_hidden(res.text, "ctl00$MainContent$hiddenCsrf")
            logger.info("Session initialized successfully")

        except Exception as e:
            logger.error(f"Session initialization failed: {e}")
            raise



    def extract_hidden(self, html, field):
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("input", {"name": field})
        return tag["value"] if tag else ""

    def fetch_case(self, case_id):

        startTime = time.time()
        try:
            # Generate the date at the exact moment of the request
            current_date = date.today().strftime("%d/%m/%Y")

            payload = {
                "ctl00$MainContent$sm1" : "ctl00$MainContent$UpdatePanelSearch|ctl00$MainContent$btnSearchCase",
                "__EVENTTARGET" : "",
                "__EVENTARGUMENT" : "",
                "__LASTFOCUS" : "",
                "__VIEWSTATE" : self.viewstate,
                "__VIEWSTATEGENERATOR" : self.generator,
                "ctl00$MainContent$hiddenCsrf" : self.csrf,
                "ctl00$MainContent$rbCaseSearch" : "0", 
                "ctl00$MainContent$txtCompID" : case_id, 
                "ctl00$MainContent$txtPetitionerName" : "", 
                "ctl00$MainContent$txtPartivadiName" : "", 
                "ctl00$MainContent$ddlDistrict" : "", 
                "ctl00$MainContent$ddlAct" : "", 
                "ctl00$MainContent$ddlCaseTypeGroup" : "", 
                "ctl00$MainContent$ddlCasePurpose" : "", 
                "ctl00$MainContent$txthrDate" : "", 
                "ctl00$MainContent$txtInstfrmDt" : "", 
                "ctl00$MainContent$txtInsttoDt" : "", 
                "ctl00$MainContent$txtCaseAdvocate" : "", 
                "ctl00$MainContent$hfCaseAdvRegNo" : "", 
                "ctl00$MainContent$rblDecisionStatus" : "FALSE", 
                "ctl00$MainContent$rbCauseList" : "0", 
                "ctl00$MainContent$txtBorCSDate" : current_date, 
                "ctl00$MainContent$txtBorHDate" : "", 
                "ctl00$MainContent$rblBorCauseListCourt" : "BOARD OF REVENUE,AJMER", 
                "ctl00$MainContent$rblAdvCauseListCourt" : "ALL", 
                "ctl00$MainContent$rblAdvCauseListType" : "ALL", 
                "ctl00$MainContent$txtCauseAdvocate" : "", 
                "ctl00$MainContent$hfCauseAdvocate" : "", 
                "ctl00$MainContent$rblOldCauseListType" : "ALL", 
                "ctl00$MainContent$rbDecision" : "0", 
                "ctl00$MainContent$txtCaseID" : "", 
                "ctl00$MainContent$ddlDecDistrict" : "", 
                "ctl00$MainContent$ddlDecCaseType" : "", 
                "ctl00$MainContent$ddlDecAct" : "", 
                "ctl00$MainContent$txtDecFromDate" : "", 
                "ctl00$MainContent$txtDecToDate" : "", 
                "ctl00$MainContent$txtDecMember" : "", 
                "ctl00$MainContent$rblWorthWise" : "F", 
                "ctl00$MainContent$txtAppCaseID" : "", 
                "ctl00$MainContent$hfActiveCase" : "0", 
                "ctl00$MainContent$selected_tab" : "#tabCaseSearch", 
                "ctl00$hdnIP" : "", 
                "__ASYNCPOST" : "true",
                "ctl00$MainContent$btnSearchCase": "देखें"
            }

            response = self.session.post(URL, data=payload, headers=self.HEADERS)
            responseTime = time.time() - startTime

            html = self.extract_updatepanel(response.text)
            soup = BeautifulSoup(html, "html.parser")

            result = {
                "case_id": case_id,
                "appellant": self.get_value(soup, "प्रार्थी"),
                "respondent": self.get_value(soup, "अप्रार्थी"),
                "hearing_date": self.get_value(soup, "सुनवाई/निर्णय दिनांक"),
                "bench": self.get_value(soup, "बेंच")
            }

            log_scrape_event(logger, case_id=case_id, task_type="fetch_case",
                         result="success", response_time=responseTime)
            return result
        
        except Exception as e:
            responseTime = time.time() - startTime
            log_scrape_event(logger, case_id=case_id, task_type="fetch_case",
                            result="failed", response_time=responseTime,
                            error_message=str(e))
            return None
        



    def extract_updatepanel(self, text):
        parts = text.split("|")
        for i in range(len(parts)):
            if parts[i] == "updatePanel":
                return parts[i+2]
        return None

    def get_value(self, soup, label):
        target = soup.find("td", string=re.compile(label))
        if target:
            return target.find_next_sibling("td").text.strip()
        return None