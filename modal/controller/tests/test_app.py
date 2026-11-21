def test_the_controller_deploys_as_t3modal_controller():
    from app import app

    assert app.name == "t3modal-controller"


def test_each_credential_kind_has_its_own_named_secret():
    from app import credential_secret_name

    assert credential_secret_name("claude") == "t3modal-claude"
