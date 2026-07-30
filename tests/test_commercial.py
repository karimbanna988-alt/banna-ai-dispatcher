"""Tests for src/dispatcher/commercial.py -- deterministic quote/
customer-response extraction, company-facing reply preparation, and
company-question detection/answering. Every case here is grounded in
the exact example phrases from the Shadow Mode requirements; nothing is
guessed at."""

from __future__ import annotations

from dispatcher.commercial import (
    CommercialUpdate,
    CompanyQuestionType,
    CustomerResponseType,
    NegotiationState,
    QuoteType,
    answer_company_commercial_question,
    detect_company_question,
    extract_commercial_update,
    prepare_commercial_company_reply,
)


# --------------------------------------------------------------- Quote type
def test_fixed_quote():
    cu = extract_commercial_update("quoted 125")
    assert cu.quote_type == QuoteType.FIXED
    assert cu.quote_amount == "125"
    assert cu.currency is None


def test_fixed_quote_with_dollar_sign():
    cu = extract_commercial_update("quoted $125")
    assert cu.quote_type == QuoteType.FIXED
    assert cu.currency == "USD"


def test_gave_price_is_fixed():
    assert extract_commercial_update("gave price 125").quote_type == QuoteType.FIXED


def test_price_is_phrasing_is_fixed():
    assert extract_commercial_update("price is 125").quote_type == QuoteType.FIXED


def test_starting_price():
    cu = extract_commercial_update("starting 125")
    assert cu.quote_type == QuoteType.STARTING
    assert cu.quote_amount == "125"


def test_starting_at_price():
    assert extract_commercial_update("starting at 125").quote_type == QuoteType.STARTING


def test_starts_at_dollar_price():
    cu = extract_commercial_update("starts at $125")
    assert cu.quote_type == QuoteType.STARTING
    assert cu.currency == "USD"


def test_and_up_is_starting_not_fixed():
    cu = extract_commercial_update("125 and up")
    assert cu.quote_type == QuoteType.STARTING


def test_plus_is_starting_not_fixed():
    cu = extract_commercial_update("125+")
    assert cu.quote_type == QuoteType.STARTING


def test_hyphen_range():
    cu = extract_commercial_update("quoted 125-175")
    assert cu.quote_type == QuoteType.RANGE
    assert cu.quote_range_low == "125"
    assert cu.quote_range_high == "175"
    assert cu.quote_amount is None


def test_between_and_range():
    cu = extract_commercial_update("between 125 and 175")
    assert cu.quote_type == QuoteType.RANGE
    assert cu.quote_range_low == "125"
    assert cu.quote_range_high == "175"


def test_around_is_estimate():
    assert extract_commercial_update("around 125").quote_type == QuoteType.ESTIMATE


def test_estimate_keyword_is_estimate():
    assert extract_commercial_update("estimate 125").quote_type == QuoteType.ESTIMATE


def test_approximately_is_estimate():
    assert extract_commercial_update("approximately 125").quote_type == QuoteType.ESTIMATE


def test_no_amount_when_no_number_present():
    cu = extract_commercial_update("customer wants a discount")
    assert cu.quote_type == QuoteType.NO_AMOUNT
    assert cu.human_review_required is False


def test_no_currency_never_invented():
    cu = extract_commercial_update("quoted 125")
    assert cu.currency is None


def test_multiple_numbers_range_wins_over_fixed():
    cu = extract_commercial_update("quoted 125 to 175 total")
    assert cu.quote_type == QuoteType.RANGE


def test_amount_never_modified_or_rounded():
    cu = extract_commercial_update("quoted 125.50")
    assert cu.quote_amount == "125.50"


def test_raw_text_always_preserved_verbatim():
    raw = "  Quoted  125,  customer will call back  "
    cu = extract_commercial_update(raw)
    assert cu.raw_technician_text == raw


# ------------------------------------------------------- Ambiguous wording
def test_ambiguous_number_produces_unknown_and_human_review():
    cu = extract_commercial_update("he said maybe 125 or so we will see")
    assert cu.quote_type == QuoteType.UNKNOWN
    assert cu.human_review_required is True
    assert cu.validation_issues


def test_negated_not_quoted_yet_is_ambiguous_not_fixed():
    """'Not quoted yet' contains no number at all -- NO_AMOUNT, not a
    false-positive FIXED/ambiguous match."""
    cu = extract_commercial_update("not quoted yet")
    assert cu.quote_type == QuoteType.NO_AMOUNT
    assert cu.human_review_required is False


def test_blank_text_requires_human_review():
    cu = extract_commercial_update("")
    assert cu.human_review_required is True
    assert cu.validation_issues


# --------------------------------------------------------- Customer response
def test_will_call_back():
    cu = extract_commercial_update("quoted 125, customer will call back")
    assert cu.customer_response == CustomerResponseType.WILL_CALL_BACK
    assert cu.negotiation_state == NegotiationState.FOLLOW_UP_REQUIRED
    assert cu.follow_up_required is True


def test_thinking():
    cu = extract_commercial_update("starting 125, customer is thinking")
    assert cu.customer_response == CustomerResponseType.THINKING
    assert cu.negotiation_state == NegotiationState.WAITING_FOR_CUSTOMER


def test_too_expensive():
    cu = extract_commercial_update("customer says too expensive")
    assert cu.customer_response == CustomerResponseType.TOO_EXPENSIVE


def test_shopping_around():
    cu = extract_commercial_update("customer is shopping around")
    assert cu.customer_response == CustomerResponseType.SHOPPING_AROUND


def test_requested_discount():
    cu = extract_commercial_update("customer wants a discount")
    assert cu.customer_response == CustomerResponseType.REQUESTED_DISCOUNT


def test_accepted():
    cu = extract_commercial_update("customer accepted")
    assert cu.customer_response == CustomerResponseType.ACCEPTED
    assert cu.negotiation_state == NegotiationState.ACCEPTED


def test_declined():
    cu = extract_commercial_update("customer declined")
    assert cu.customer_response == CustomerResponseType.DECLINED
    assert cu.negotiation_state == NegotiationState.LOST_ON_PRICE


def test_no_response_after_quote():
    cu = extract_commercial_update("no response after quote")
    assert cu.customer_response == CustomerResponseType.NO_RESPONSE


# ------------------------------------------------- Critical business rules
def test_quote_alone_does_not_imply_acceptance():
    cu = extract_commercial_update("quoted 125")
    assert cu.customer_response != CustomerResponseType.ACCEPTED
    assert cu.negotiation_state != NegotiationState.ACCEPTED


def test_will_call_back_does_not_imply_cancellation():
    cu = extract_commercial_update("quoted 125, customer will call back")
    assert cu.negotiation_state not in (NegotiationState.LOST_ON_PRICE,)


def test_thinking_does_not_imply_cancellation():
    cu = extract_commercial_update("starting 125, customer is thinking")
    assert cu.negotiation_state != NegotiationState.LOST_ON_PRICE


def test_too_expensive_does_not_automatically_imply_cancellation():
    cu = extract_commercial_update("customer says too expensive")
    assert cu.negotiation_state == NegotiationState.NEGOTIATING
    assert cu.negotiation_state != NegotiationState.LOST_ON_PRICE


def test_shopping_around_does_not_automatically_imply_cancellation():
    cu = extract_commercial_update("customer is shopping around")
    assert cu.negotiation_state != NegotiationState.LOST_ON_PRICE


# --------------------------------------------------------- Reply preparation
def test_reply_fixed_quote_matches_spec_example():
    cu = extract_commercial_update("quoted 125")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician quoted $125."
    assert reply.confirmed is True


def test_reply_starting_quote_matches_spec_example():
    cu = extract_commercial_update("starting 125")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician advised pricing starts at $125."


def test_reply_fixed_quote_with_will_call_back_matches_spec_example():
    cu = extract_commercial_update("quoted 125, customer said he will call back")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician quoted $125. Customer said they will call back."


def test_reply_starting_quote_with_too_expensive_matches_spec_example():
    cu = extract_commercial_update("starting 125, customer said too expensive")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician advised pricing starts at $125. Customer said the price was too high."


def test_reply_range_quote():
    cu = extract_commercial_update("between 125 and 175")
    reply = prepare_commercial_company_reply("Lucky", cu)
    assert reply.reply == "Technician quoted a range of $125 to $175."


def test_reply_estimate_quote():
    cu = extract_commercial_update("around 125")
    reply = prepare_commercial_company_reply("Lucky", cu)
    assert reply.reply == "Technician gave an estimate of $125."


def test_reply_no_amount():
    cu = extract_commercial_update("no price given yet")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician has not provided a price yet."


def test_reply_no_amount_with_customer_response_includes_both_clauses():
    cu = extract_commercial_update("customer wants a discount")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.reply == "Technician has not provided a price yet. Customer asked for a discount."


def test_reply_ambiguous_never_confirmed():
    cu = extract_commercial_update("he said maybe 125 or so we will see")
    reply = prepare_commercial_company_reply("AGN", cu)
    assert reply.confirmed is False
    assert reply.reply is None


def test_reply_never_invents_currency_symbol_when_source_gave_none_and_only_uses_business_default():
    """The business-wide USD default is documented and disclosed (this
    is a single-country business) -- it is applied only at reply-
    preparation time, never during extraction (extraction's `currency`
    field stays None for "quoted 125")."""
    cu = extract_commercial_update("quoted 125")
    assert cu.currency is None
    reply = prepare_commercial_company_reply("AGN", cu)
    assert "$125" in reply.reply


# ------------------------------------------------------------ Corrections
def test_corrected_quote_is_a_distinct_call_from_original():
    """A dispatcher-corrected quote is a separate extraction call, never
    mutating the original -- corrections happen at the application-
    service/persistence layer (see test_application_service.py), not
    inside this pure function."""
    original = extract_commercial_update("quoted 125")
    corrected = extract_commercial_update("quoted 150")
    assert original.quote_amount == "125"
    assert corrected.quote_amount == "150"
    assert original is not corrected


# --------------------------------------------------------- Company questions
def test_detect_did_you_quote():
    assert detect_company_question("Did you quote?") == CompanyQuestionType.DID_YOU_QUOTE


def test_detect_what_price():
    assert detect_company_question("What price was given?") == CompanyQuestionType.WHAT_PRICE


def test_detect_customer_agreed():
    assert detect_company_question("Did customer agree?") == CompanyQuestionType.CUSTOMER_AGREED


def test_detect_customer_response_status():
    assert detect_company_question("Any response from customer?") == CompanyQuestionType.CUSTOMER_RESPONSE_STATUS
    assert detect_company_question("Did anyone call?") == CompanyQuestionType.CUSTOMER_RESPONSE_STATUS


def test_detect_can_do_less():
    assert detect_company_question("Can technician do less?") == CompanyQuestionType.CAN_DO_LESS


def test_detect_unknown_question():
    assert detect_company_question("totally unrelated text") == CompanyQuestionType.UNKNOWN


def test_no_verified_quote_returns_human_review_needed():
    answer = answer_company_commercial_question(CompanyQuestionType.DID_YOU_QUOTE, None)
    assert answer.confirmed is False
    assert "no verified price" in answer.reason


def test_can_do_less_never_negotiates_automatically():
    latest = extract_commercial_update("quoted 125")
    answer = answer_company_commercial_question(CompanyQuestionType.CAN_DO_LESS, latest)
    assert answer.confirmed is False
    assert "cannot negotiate" in answer.reason


def test_did_you_quote_answers_from_latest_verified_quote():
    latest = extract_commercial_update("starting 100")
    answer = answer_company_commercial_question(CompanyQuestionType.DID_YOU_QUOTE, latest)
    assert answer.confirmed is True
    assert "$100" in answer.reply


def test_customer_agreed_question_reflects_accepted():
    latest = extract_commercial_update("quoted 125, customer accepted")
    answer = answer_company_commercial_question(CompanyQuestionType.CUSTOMER_AGREED, latest)
    assert answer.confirmed is True
    assert "accepted" in answer.reply.lower()


def test_customer_agreed_question_unconfirmed_without_evidence():
    latest = extract_commercial_update("quoted 125")
    answer = answer_company_commercial_question(CompanyQuestionType.CUSTOMER_AGREED, latest)
    assert answer.confirmed is False
