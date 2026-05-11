Feature: TOML config loader

  As a Voyager operator
  I want to load app configuration from a TOML file
  So that I can configure multiple GitHub Apps without environment variable sprawl

  Scenario: Load valid TOML with two apps
    Given the TOML config file "valid_two_apps.toml"
    When the config is loaded
    Then the apps dict has 2 entries
    And the apps dict contains slug "iterwheel-blueprint"
    And the apps dict contains slug "iterwheel-stack"

  Scenario: Missing required app_id raises ValueError
    Given the TOML config file "missing_app_id.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "app_id"

  Scenario: Missing required private_key_path raises ValueError
    Given the TOML config file "missing_private_key_path.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "private_key_path"

  Scenario: Tilde in private_key_path is expanded
    Given the TOML config file "tilde_path.toml"
    When the config is loaded
    Then the "iterwheel-blueprint" app private_key_path does not start with "~"

  Scenario: webhook_secret_env defaults to derived name when absent
    Given the TOML config file "default_webhook_secret.toml"
    When the config is loaded
    Then the "iterwheel-blueprint" app webhook_secret_env is "GITHUB_WEBHOOK_SECRET_ITERWHEEL_BLUEPRINT"

  Scenario: installations table parses into dict
    Given the TOML config file "with_installations.toml"
    When the config is loaded
    Then the "iterwheel-blueprint" app installations has key "iterwheel/voyager-sandbox" with value "55544433"

  Scenario: VOYAGER_CONFIG_PATH env override is honored
    Given the TOML config file "valid_two_apps.toml" is set via VOYAGER_CONFIG_PATH
    When the config is loaded without an explicit path
    Then the apps dict has 2 entries

  Scenario: Missing TOML file raises FileNotFoundError with the path
    Given a nonexistent config path "/tmp/voyager_nonexistent_xyz.toml"
    When the config load is attempted
    Then a FileNotFoundError is raised
