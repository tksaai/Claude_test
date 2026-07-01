from twitch_monitor.chat import parse_privmsg


def test_parse_privmsg_with_tags():
    line = (
        "@badge-info=;badges=;color=#FF0000;display-name=テストさん;"
        "emotes=25:0-4,6-10/1902:12-16;id=abc;mod=0"
        " :testuser!testuser@testuser.tmi.twitch.tv PRIVMSG #somechannel :Kappa Kappa Keepo 草\r\n"
    )
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.user == "テストさん"
    assert msg.text == "Kappa Kappa Keepo 草"
    assert msg.emote_count == 3


def test_parse_privmsg_without_tags_falls_back_to_login():
    line = ":foo!foo@foo.tmi.twitch.tv PRIVMSG #chan :hello\r\n"
    msg = parse_privmsg(line)
    assert msg is not None
    assert msg.user == "foo"
    assert msg.text == "hello"
    assert msg.emote_count == 0


def test_non_privmsg_returns_none():
    assert parse_privmsg(":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!\r\n") is None
    assert parse_privmsg("PING :tmi.twitch.tv\r\n") is None
