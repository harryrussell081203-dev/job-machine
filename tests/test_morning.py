"""
Tests for the morning text.

Offline. Nothing is sent.

The failure mode this guards against is not a crash. It is a text that says
something generic, or something untrue, or something that was already done -
because a daily text you stop believing is worse than no daily text.
"""
import json
import os
import sys
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inbox_watch  # noqa: E402
import job_machine as jm  # noqa: E402
import morning  # noqa: E402
import sms  # noqa: E402


def uk(year, month, day, hour):
    return datetime(year, month, day, hour, tzinfo=ZoneInfo("Europe/London"))


class TestWhatIsWaitingOnHim(unittest.TestCase):
    def test_somebody_asking_for_a_call_comes_first(self):
        """Nothing he could start today beats finishing something somebody
        else has already begun."""
        state = {"jobs": {"a": {"company": "TMM", "contact_name": "Cammy",
                                "wants_a_word": True,
                                "contact_mobile": "+447700900001"},
                          "b": {"company": "X", "status": "portal_review"}}}
        items = morning.waiting_on_harry(state)
        self.assertEqual(items[0]["kind"], "call")
        self.assertIn("Cammy", items[0]["text"])
        self.assertIn("+447700900001", items[0]["text"])

    def test_an_interview_outranks_a_call(self):
        state = {"jobs": {
            "a": {"company": "TMM", "wants_a_word": True},
            "b": {"company": "Hydro", "reply_category": "interview_invite"}}}
        self.assertEqual(morning.waiting_on_harry(state)[0]["kind"], "interview")

    def test_a_call_already_made_is_not_chased(self):
        state = {"jobs": {"a": {"company": "TMM", "wants_a_word": True,
                                "call_made_at": jm.now()}}}
        self.assertEqual(morning.waiting_on_harry(state), [])

    def test_the_number_is_taken_from_the_phone_book_if_the_job_lacks_one(self):
        state = {"jobs": {"a": {"company": "TMM Recruitment",
                                "wants_a_word": True}},
                 "contact_numbers": {jm.company_key("TMM Recruitment"):
                                     {"numbers": ["+441224327030"]}}}
        self.assertIn("+441224327030", morning.waiting_on_harry(state)[0]["text"])

    def test_questions_are_counted_not_listed(self):
        state = {"jobs": {str(i): {"company": f"F{i}",
                                   "reply_category": "question"}
                          for i in range(3)}}
        text = morning.waiting_on_harry(state)[0]["text"]
        self.assertIn("+2 more", text)

    def test_nothing_live_means_nothing_live(self):
        self.assertEqual(morning.waiting_on_harry({"jobs": {}}), [])


class TestTheStandingGoals(unittest.TestCase):
    def setUp(self):
        self.goals = morning.load_goals()

    def test_the_shipped_file_parses_and_is_ordered(self):
        self.assertTrue(self.goals)
        priorities = [g.get("priority", 99) for g in self.goals]
        self.assertEqual(priorities, sorted(priorities))

    def test_every_goal_says_where_it_came_from(self):
        """A goal with no source is a guess about somebody's life."""
        for goal in self.goals:
            with self.subTest(goal=goal["id"]):
                self.assertTrue(goal.get("source"))
                self.assertTrue(goal.get("next_action"))
                self.assertTrue(goal.get("why"))

    def test_the_thing_blocking_the_whole_market_is_first(self):
        self.assertEqual(self.goals[0]["id"], "offshore_tickets")

    def test_one_goal_a_day_not_a_list(self):
        state = {}
        goal = morning.todays_goal(state, self.goals, uk(2026, 8, 5, 7))
        self.assertIsInstance(goal, dict)

    def test_the_rotation_moves_on_and_does_not_repeat(self):
        state = {}
        seen = []
        for _ in range(3):
            goal = morning.todays_goal(state, self.goals, uk(2026, 8, 5, 7))
            seen.append(goal["id"])
            morning.advance_rotation(state, self.goals)
        self.assertEqual(len(set(seen)), 3)

    def test_a_goal_pinned_to_a_weekday_takes_that_day(self):
        goals = [{"id": "any", "next_action": "a", "priority": 1},
                 {"id": "monday", "next_action": "b", "priority": 9,
                  "weekday": 0}]
        monday = uk(2026, 8, 3, 7)
        self.assertEqual(morning.todays_goal({}, goals, monday)["id"], "monday")

    def test_an_inactive_goal_is_ignored(self):
        with mock.patch.object(morning, "GOALS_PATH", "/nonexistent.json"):
            self.assertEqual(morning.load_goals(), [])


class TestTheText(unittest.TestCase):
    def goals(self):
        return [{"id": "g", "next_action": "Apply for the provisional licence",
                 "priority": 1}]

    def test_it_fits_in_a_text(self):
        state = {"jobs": {str(i): {"company": f"Firm {i}", "wants_a_word": True,
                                   "contact_name": f"Person {i}",
                                   "contact_mobile": "+447700900001"}
                          for i in range(5)}}
        body = morning.compose(state, self.goals(), uk(2026, 8, 5, 7))
        self.assertLessEqual(len(body), 320)

    def test_live_work_comes_before_the_standing_goal(self):
        state = {"jobs": {"a": {"company": "TMM", "contact_name": "Cammy",
                                "wants_a_word": True}}}
        body = morning.compose(state, self.goals(), uk(2026, 8, 5, 7))
        self.assertLess(body.index("Cammy"), body.index("provisional"))

    def test_a_quiet_day_still_gives_him_one_thing(self):
        body = morning.compose({"jobs": {}}, self.goals(), uk(2026, 8, 5, 7))
        self.assertIn("provisional", body)
        self.assertTrue(body.startswith("Today: 1."))

    def test_nothing_at_all_sends_nothing(self):
        """No live work and no goals is silence, not filler."""
        self.assertEqual(morning.compose({"jobs": {}}, [], uk(2026, 8, 5, 7)), "")

    def test_it_never_sends_more_than_three_things(self):
        state = {"jobs": {str(i): {"company": f"F{i}", "wants_a_word": True}
                          for i in range(6)}}
        body = morning.compose(state, self.goals(), uk(2026, 8, 5, 7))
        self.assertNotIn("4.", body)


class TestWhenItGoes(unittest.TestCase):
    def test_it_goes_at_the_uk_morning_hour(self):
        self.assertTrue(morning.due_today(uk(2026, 8, 5, 7)))

    def test_the_other_cron_of_the_pair_does_not_send(self):
        """Both 06:45 and 07:45 UTC fire; only one of them is 07:45 in
        Aberdeen, and the other must do nothing."""
        self.assertFalse(morning.due_today(uk(2026, 8, 5, 6)))
        self.assertFalse(morning.due_today(uk(2026, 8, 5, 8)))

    def test_it_goes_once_a_day(self):
        state = {"jobs": {}, morning.SENT_ON: jm.today()}
        with mock.patch.object(sms, "API_KEY", "k"), \
             mock.patch.object(sms, "send") as send:
            self.assertEqual(morning.run(state, send=True,
                                         when=uk(2026, 8, 5, 7)), 0)
        send.assert_not_called()

    def test_asking_by_hand_ignores_the_clock(self):
        state = {"jobs": {"a": {"company": "TMM", "wants_a_word": True}}}
        with mock.patch.object(sms, "API_KEY", "k"), \
             mock.patch.object(sms, "send") as send:
            self.assertEqual(morning.run(state, send=True, force=True), 1)
        send.assert_called_once()

    def test_a_dry_run_sends_nothing(self):
        state = {"jobs": {"a": {"company": "TMM", "wants_a_word": True}}}
        with mock.patch.object(sms, "send") as send:
            morning.run(state, send=False, force=True)
        send.assert_not_called()
        self.assertNotIn(morning.SENT_ON, state)

    def test_a_failed_send_does_not_mark_the_day_as_done(self):
        state = {"jobs": {"a": {"company": "TMM", "wants_a_word": True}}}
        with mock.patch.object(sms, "API_KEY", "k"), \
             mock.patch.object(sms, "send", side_effect=RuntimeError("502")):
            morning.run(state, send=True, force=True)
        self.assertNotIn(morning.SENT_ON, state)


class TestReadingTheInbox(unittest.TestCase):
    """It reads Harry's mail now, because he asked for it. What makes that
    safe is not restraint about what it reads - it is that the only address
    it can ever text is his own handset."""

    MESSAGES = [
        {"key": "<1@x>", "who": "Cammy Keith", "address": "ckeith@tmm.com",
         "subject": "Re: your CV", "at": jm.now(),
         "body": "Hi Harry, can you send me your availability for Thursday?"},
        {"key": "<2@x>", "who": "Graham Brown", "address": "g@frs.co.uk",
         "subject": "Re: registering", "at": jm.now(),
         "body": "Please send a word copy to my colleague Will."},
    ]

    def test_the_filters_are_the_watchers_own(self):
        """One definition, not two. The morning brief and the two-hourly
        watcher reading the same mail by different rules is how they end up
        disagreeing about what is waiting on him."""
        self.assertIs(morning.NOT_A_PERSON, inbox_watch.NOT_A_PERSON)
        self.assertIs(morning.asks_something, inbox_watch.asks_something)

    def test_it_reports_what_the_register_says_is_outstanding(self):
        state = {}
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            actions = morning.inbox_actions(state, self.MESSAGES)
        self.assertTrue(actions)
        self.assertTrue(any("Cammy Keith" in a["text"] for a in actions))

    def test_something_he_has_already_dealt_with_is_not_raised_again(self):
        """The whole reason for a register. Before it, the same email was
        re-read and re-texted about every single morning."""
        state = {}
        with mock.patch.object(jm, "GEMINI_API_KEY", ""):
            morning.inbox_actions(state, self.MESSAGES)
        for key in list(state["inbox"]):
            inbox_watch.mark_done(state, key)
        self.assertEqual(morning.inbox_actions(state, self.MESSAGES), [])

    def test_a_dead_inbox_does_not_stop_the_text(self):
        """The state file and the goals are still worth sending on their own."""
        with mock.patch.object(morning, "inbox_actions",
                               side_effect=RuntimeError("imap down")):
            body = morning.compose({"jobs": {}}, morning.load_goals(),
                                   uk(2026, 8, 5, 7))
        self.assertTrue(body)

    def test_it_can_still_be_told_not_to_read_the_mail(self):
        with mock.patch.object(morning, "inbox_actions") as inbox:
            morning.compose({"jobs": {}}, morning.load_goals(),
                            uk(2026, 8, 5, 7), inbox=False)
        inbox.assert_not_called()

    def test_the_only_number_it_can_text_is_his_own(self):
        """This is what makes reading the mail safe: there is no recipient
        argument to get wrong."""
        import inspect
        self.assertNotIn("to", inspect.signature(sms.alert_harry).parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestItStopsNagging(unittest.TestCase):
    """Nothing here can tell whether Harry answered an email - he answers from
    his own inbox and the machine never sees it. So an item he has already
    dealt with would be repeated every morning forever, and the fastest way to
    make a daily text worthless is to nag about something that is done."""

    def old(self, days):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def test_a_question_from_this_week_is_still_chased(self):
        state = {"jobs": {"a": {"company": "Angus Council",
                                "reply_category": "question",
                                "replied_at": self.old(2)}}}
        self.assertTrue(morning.waiting_on_harry(state))

    def test_a_question_from_last_month_is_left_alone(self):
        state = {"jobs": {"a": {"company": "Angus Council",
                                "reply_category": "question",
                                "replied_at": self.old(30)}}}
        self.assertEqual(morning.waiting_on_harry(state), [])

    def test_something_with_no_date_is_kept(self):
        """Better to mention it once too often than to drop it silently."""
        state = {"jobs": {"a": {"company": "TMM", "wants_a_word": True}}}
        self.assertTrue(morning.waiting_on_harry(state))
