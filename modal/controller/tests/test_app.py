def test_the_controller_deploys_as_t3modal_controller():
    from app import app

    assert app.name == "t3modal-controller"
