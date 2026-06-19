"""AccountTracker.get_compliance_status is the dashboard-facing alias."""
from core.account_tracker import AccountTracker


def test_get_compliance_status_matches_check_compliance():
    tracker = AccountTracker()
    tracker.initialize_account(
        account_id="22182502",
        account_name="PRAC-V2-14334",
        account_type="practice",
        starting_balance=172422.23,
    )
    via_alias = tracker.get_compliance_status("22182502")
    via_check = tracker.check_compliance("22182502")
    assert via_alias == via_check
    assert via_alias["dll_limit"] == 1000.0
    assert via_alias["mll_limit"] == 2500.0
