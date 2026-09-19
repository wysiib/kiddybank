from app import coins


def test_a_positive_amount_is_never_zero_coins():
    assert coins.coins(0, 100) == 0
    assert coins.coins(-5, 100) == 0
    assert coins.coins(3, 5) == 1  # 3 Cent still shows one small coin
    assert coins.coins(1, 100) == 1
    assert coins.coins(150, 100) == 2  # nearest, halves up
    assert coins.coins(149, 100) == 1


def test_one_stack_never_draws_more_than_the_cap():
    assert coins.coins(10**9, 100) == coins.MAX_COINS


def test_unit_is_the_smallest_ladder_step_that_fits_the_cap():
    assert coins.coin_unit([1000]) == 100  # 10 EUR = 10 coins of 1 EUR, exactly the cap
    assert coins.coin_unit([1100]) == 200  # 11 coins would exceed 10
    assert coins.coin_unit([1000, 5000]) == 500  # the biggest stack in the scene decides for all
    assert coins.coin_unit([]) == 100
    assert coins.coin_unit([10**9]) == coins.BIG_LADDER[-1]  # above the ladder: top step, the printed amount carries the rest


def test_small_ladder():
    assert coins.coin_unit([15], coins.SMALL_LADDER, coins.SMALL_CAP) == 5  # 3 coins
    assert coins.coin_unit([100], coins.SMALL_LADDER, coins.SMALL_CAP) == 5  # exactly 20 coins
    assert coins.coin_unit([110], coins.SMALL_LADDER, coins.SMALL_CAP) == 10


def test_time_unit_goes_day_week_month():
    assert coins.time_unit(7) == 1
    assert coins.time_unit(13) == 1
    assert coins.time_unit(14) == 7
    assert coins.time_unit(30) == 7
    assert coins.time_unit(91) == 7
    assert coins.time_unit(92) == 30
    assert coins.time_unit(365) == 30


def test_time_units_keep_the_fraction_of_the_last_calendar():
    assert coins.time_units(7, 7) == 1
    assert coins.time_units(30, 7) == 4.29  # 4 weeks and 2 days
    assert coins.time_units(90, 7) == 12.86
    assert coins.time_units(10**6, 30) == coins.MAX_UNITS


def test_slot_fill_is_whole_slots_plus_a_partly_filled_one():
    assert coins.slot_fill(64) == (6, 40)
    assert coins.slot_fill(0) == (0, 0)
    assert coins.slot_fill(100) == (10, 0)
    assert coins.slot_fill(33) == (3, 30)


def test_shortfall_always_shows_a_missing_coin():
    s = coins.shortfall(1000, 9999)
    assert (s["unit"], s["have"], s["gap"]) == (1000, 1, 9)
    close = coins.shortfall(150, 151)  # both round to 2 coins, gap is 0, floor ensures 1 dashed coin
    assert close["have"] == 2
    assert close["gap"] == 1
    assert (s["have_cents"], s["need_cents"]) == (1000, 9999)
