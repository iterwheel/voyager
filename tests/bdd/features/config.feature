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

  Scenario: webhook_secret_env is derived from slug name (convention-only)
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

  Scenario: VOYAGER_CONFIG_PATH set to a nonexistent path fails fast (no fallback) (Codex round 2 P2)
    Given VOYAGER_CONFIG_PATH is set to nonexistent path "/tmp/voyager_typo_xyz.toml"
    When the config load is attempted via the env override
    Then a FileNotFoundError is raised
    And the error message mentions "VOYAGER_CONFIG_PATH"

  Scenario: VOYAGER_CONFIG_PATH tilde prefix is expanded before existence check (Codex round 3 P2)
    Given VOYAGER_CONFIG_PATH is set to a tilde path resolving to a valid config
    When the config is loaded without an explicit path
    Then the apps dict has 2 entries

  # ---------------------------------------------------------------------------
  # Profile parsing (7B-2)
  # ---------------------------------------------------------------------------

  Scenario: Load TOML with five named profiles
    Given the TOML config file "valid_with_profiles.toml"
    When the config is loaded
    Then the profiles dict has 5 entries
    And the profiles dict contains profile "pro"
    And the profiles dict contains profile "pro_max"
    And the profiles dict contains profile "pro_fast"
    And the profiles dict contains profile "flash"
    And the profiles dict contains profile "flash_fast"

  Scenario: Profile fields parse correctly for pro
    Given the TOML config file "valid_with_profiles.toml"
    When the config is loaded
    Then profile "pro" has model "deepseek-v4-pro"
    And profile "pro" has thinking true
    And profile "pro" has reasoning_effort None
    And profile "pro" has max_diff_chars 20000
    And profile "pro" has min_confidence 0.78

  Scenario: Profile fields parse correctly for pro_max
    Given the TOML config file "valid_with_profiles.toml"
    When the config is loaded
    Then profile "pro_max" has model "deepseek-v4-pro"
    And profile "pro_max" has thinking true
    And profile "pro_max" has reasoning_effort "high"
    And profile "pro_max" has max_diff_chars 40000
    And profile "pro_max" has min_confidence 0.85

  Scenario: Profile fields parse correctly for flash_fast
    Given the TOML config file "valid_with_profiles.toml"
    When the config is loaded
    Then profile "flash_fast" has model "deepseek-v4-flash"
    And profile "flash_fast" has thinking false
    And profile "flash_fast" has reasoning_effort None
    And profile "flash_fast" has max_diff_chars 8000
    And profile "flash_fast" has min_confidence 0.90

  Scenario: default_profile is parsed from voyager section
    Given the TOML config file "valid_with_profiles.toml"
    When the config is loaded
    Then the default_profile is "pro"

  Scenario: Config without profiles section has empty profiles dict
    Given the TOML config file "valid_two_apps.toml"
    When the config is loaded
    Then the profiles dict has 0 entries
    And the default_profile is None

  Scenario: Profile missing model raises ValueError
    Given the TOML config file "profile_missing_model.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "model"

  Scenario: Profile thinking as string raises ValueError (TOML bool coercion guard)
    Given the TOML config file "profile_thinking_string.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "thinking"
    And the error message mentions "boolean"

  Scenario: Profile reasoning_effort outside allowlist raises ValueError
    Given the TOML config file "profile_reasoning_effort_invalid.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "reasoning_effort"

  Scenario: Profile reasoning_effort "max" parses (DeepSeek V4 max-effort tier, Codex PR #10 P2)
    Given the TOML config file "profile_reasoning_effort_max.toml"
    When the config is loaded
    Then the profiles dict contains profile "pro_max_effort"
    And profile "pro_max_effort" has reasoning_effort "max"

  Scenario: Profile with thinking=false and reasoning_effort raises ValueError (V4 coupling)
    Given the TOML config file "profile_thinking_false_with_effort.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "reasoning_effort"
    And the error message mentions "thinking"

  Scenario: Profile min_confidence at 0.0 raises ValueError (out of range)
    Given the TOML config file "profile_min_confidence_out_of_range.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "min_confidence"

  Scenario: Profile max_diff_chars at 0 raises ValueError (out of range)
    Given the TOML config file "profile_max_diff_chars_zero.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "max_diff_chars"

  Scenario: Profile model as integer raises ValueError (type guard N1)
    Given the TOML config file "profile_model_int.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "model"
    And the error message mentions "int"

  Scenario: Profile max_diff_chars as boolean raises ValueError (type guard N2)
    Given the TOML config file "profile_max_diff_chars_bool.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "max_diff_chars"
    And the error message mentions "bool"

  Scenario: Profile min_confidence as string raises ValueError (type guard N3)
    Given the TOML config file "profile_min_confidence_string.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "min_confidence"
    And the error message mentions "str"

  Scenario: default_profile referencing nonexistent profile raises ValueError
    Given the TOML config file "default_profile_missing.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "ghost"

  Scenario: Profile entry as scalar string raises ValueError (schema typo guard)
    Given the TOML config file "profile_scalar_entry.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "pro"
    And the error message mentions "must be a TOML table"

  # ---------------------------------------------------------------------------
  # [voyager].deepseek_api_key field (load_config is pure — no env mutation;
  # consumers combine cfg.deepseek_api_key with os.environ themselves).
  # Trinity round 0 retired the env-mutation path (4/4 reviewer SRP/test-leak).
  # ---------------------------------------------------------------------------

  Scenario: [voyager].deepseek_api_key populates cfg.deepseek_api_key
    Given VOYAGER_DEEPSEEK_API_KEY is not set in env
    And the TOML config file "voyager_section_with_api_key.toml"
    When the config is loaded
    Then the config.deepseek_api_key is "sk-toml-fixture-value"
    And VOYAGER_DEEPSEEK_API_KEY env var is unset

  Scenario: load_config does not mutate VOYAGER_DEEPSEEK_API_KEY when env is already set
    Given VOYAGER_DEEPSEEK_API_KEY is set in env to "sk-env-preexisting"
    And the TOML config file "voyager_section_with_api_key.toml"
    When the config is loaded
    Then the config.deepseek_api_key is "sk-toml-fixture-value"
    And VOYAGER_DEEPSEEK_API_KEY env var equals "sk-env-preexisting"

  Scenario: Config without [voyager].deepseek_api_key leaves field None (env-isolated)
    Given VOYAGER_DEEPSEEK_API_KEY is not set in env
    And the TOML config file "valid_two_apps.toml"
    When the config is loaded
    Then the config.deepseek_api_key is None
    And VOYAGER_DEEPSEEK_API_KEY env var is unset

  Scenario: Whitespace-only [voyager].deepseek_api_key is treated as None
    Given VOYAGER_DEEPSEEK_API_KEY is not set in env
    And the TOML config file "voyager_section_api_key_whitespace.toml"
    When the config is loaded
    Then the config.deepseek_api_key is None
    And VOYAGER_DEEPSEEK_API_KEY env var is unset

  Scenario: [voyager].deepseek_api_key as integer raises ValueError
    Given the TOML config file "voyager_section_api_key_int.toml"
    When the config load is attempted
    Then a ValueError is raised mentioning "deepseek_api_key"
    And the error message mentions "string"

  Scenario: Loading config twice with different deepseek_api_key values reflects the latest TOML
    Given VOYAGER_DEEPSEEK_API_KEY is not set in env
    And the TOML config file "voyager_section_with_api_key.toml"
    When the config is loaded then loaded again with "valid_two_apps.toml"
    Then the second config.deepseek_api_key is None
    And VOYAGER_DEEPSEEK_API_KEY env var is unset
