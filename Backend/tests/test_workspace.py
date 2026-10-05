def test_packages_import_with_same_version():
    import tf_agent
    import tf_backend
    import tf_db

    assert tf_db.__version__ == tf_agent.__version__ == tf_backend.__version__ == "0.1.0"
