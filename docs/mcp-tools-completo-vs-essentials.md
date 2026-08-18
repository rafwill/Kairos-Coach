# MCP Garmin: Completo vs Essentials

- Total tools (completo): 126
- Tools en essentials: 37

## Tabla completa

| Tool | Qué hace (resumen) | Módulo | Essentials |
|---|---|---|---|
| add_body_composition | Add body composition data | data_management | NO |
| add_gear_to_activity | Associate gear with an activity | gear_management | NO |
| add_hydration_data | Add hydration data | data_management | NO |
| add_weigh_in | Add a new weight measurement | weight_management | NO |
| add_weigh_in_with_timestamps | Add a new weight measurement with specific timestamps | weight_management | NO |
| count_activities | Get total count of activities in the user's Garmin account | activity_management | NO |
| create_custom_food | Create a custom food in the user's Garmin nutrition library | nutrition | NO |
| create_strength_workout | Create a strength workout and upload it to Garmin Connect. | workout_builders | NO |
| create_walk_run_workout | Create a walk/run interval workout and upload it to Garmin Connect. | workout_builders | NO |
| create_z2_walk_workout | Create a steady Z2 walking workout and upload it to Garmin Connect. | workout_builders | NO |
| delete_course | Delete a course from Garmin Connect. | courses | NO |
| delete_food_log | Delete a food log entry | nutrition | NO |
| delete_weigh_ins | Delete weight measurements for a specific date | weight_management | NO |
| delete_workout | Delete a workout from Garmin Connect | workouts | NO |
| delete_workouts | Delete multiple workouts from Garmin Connect in a single call | workouts | NO |
| download_activity_file | Download an activity and save it to disk as a file. | activity_analysis | NO |
| download_workout | Download a workout as a FIT file | workouts | NO |
| get_activities | Get activities with pagination support. | activity_management | SI |
| get_activities_by_date | Get activities between specified dates with pagination support. | activity_management | SI |
| get_activities_fordate | Get activities for a specific date | activity_management | SI |
| get_activity | Get detailed information for a single activity. | activity_management | SI |
| get_activity_exercise_sets | Get exercise sets for strength training activities | activity_management | NO |
| get_activity_fit_data | Download and parse FIT file for an activity to expose advanced cycling data. | activity_analysis | NO |
| get_activity_gear | Get gear data used for an activity | activity_management | NO |
| get_activity_hr_in_timezones | Get heart rate data in different time zones for an activity | activity_management | SI |
| get_activity_power_in_timezones | Get power distribution across training zones for an activity. | activity_management | NO |
| get_activity_split_summaries | Get split summaries for an activity | activity_management | NO |
| get_activity_splits | Get splits for an activity | activity_management | NO |
| get_activity_typed_splits | Get typed splits for an activity | activity_management | NO |
| get_activity_types | Get all available activity types | activity_management | NO |
| get_activity_weather | Get weather data for an activity | activity_management | NO |
| get_adhoc_challenges | Get user-created social/group challenges (e.g., step competitions with friends) | challenges | NO |
| get_all_day_events | Get daily wellness events data | health_wellness | SI |
| get_all_day_stress | Get all-day stress data | health_wellness | SI |
| get_available_badge_challenges | Get official Garmin badge challenges available to join | challenges | NO |
| get_badge_challenges | Get all badge challenges the user has joined (completed and in-progress) | challenges | NO |
| get_blood_pressure | Get blood pressure data | health_wellness | NO |
| get_body_battery | Get body battery data with events | health_wellness | SI |
| get_body_battery_events | Get body battery events data | health_wellness | NO |
| get_body_composition | Get body composition data for a single date or date range | health_wellness | SI |
| get_courses | List all courses saved on Garmin Connect. | courses | NO |
| get_custom_food_serving_units | Get available serving units for custom foods | nutrition | NO |
| get_custom_foods | Search or list user's custom foods | nutrition | NO |
| get_cycling_ftp | Get the latest cycling Functional Threshold Power (FTP) data. | training | SI |
| get_daily_steps | Get steps data for a date range | health_wellness | SI |
| get_daily_weigh_ins | Get weight measurements for a specific date | weight_management | NO |
| get_device_alarms | Get alarms from all Garmin devices | devices | NO |
| get_device_last_used | Get information about the last used Garmin device | devices | NO |
| get_device_settings | Get settings for a specific Garmin device | devices | NO |
| get_device_solar_data | Get solar data for a specific device | devices | NO |
| get_devices | Get all Garmin devices associated with the user account | devices | NO |
| get_earned_badges | Get earned badges for user | challenges | NO |
| get_endurance_score | Get endurance score data between dates | training | SI |
| get_fitnessage_data | Get fitness age data | training | SI |
| get_floors | Get floors climbed data | health_wellness | NO |
| get_full_name | Get user's full name from profile | user_profile | NO |
| get_gear | Get all gear registered with the user account | gear_management | NO |
| get_goals | Get Garmin Connect goals (active, future, or past) | challenges | NO |
| get_heart_rates | Get full heart rate time-series data | health_wellness | NO |
| get_heart_rates_summary | Get heart rate summary with essential metrics (lightweight version) | health_wellness | SI |
| get_hill_score | Get hill score data between dates | training | NO |
| get_hrv_data | Get Heart Rate Variability (HRV) data | training | SI |
| get_hrv_trend | Get HRV (Heart Rate Variability) trend over a date range. | training | SI |
| get_hydration_data | Get hydration data | health_wellness | SI |
| get_inprogress_virtual_challenges | Get in-progress virtual challenges/expeditions | challenges | NO |
| get_lactate_threshold | Get lactate threshold data | training | SI |
| get_lifestyle_logging_data | Get lifestyle logging data for a specific date | health_wellness | NO |
| get_menstrual_calendar_data | Get menstrual calendar data between specified dates | womens_health | NO |
| get_menstrual_data_for_date | Get menstrual data for a specific date | womens_health | NO |
| get_morning_training_readiness | Get morning training readiness score | health_wellness | SI |
| get_non_completed_badge_challenges | Get badge challenges currently in progress (not yet completed) | challenges | NO |
| get_nutrition_daily_food_log | Get daily food consumption records for a date | nutrition | NO |
| get_nutrition_daily_meals | Get daily meal summaries for a date | nutrition | NO |
| get_nutrition_daily_settings | Get nutrition plan/settings for a date | nutrition | NO |
| get_personal_record | Get personal records for user | challenges | SI |
| get_power_duration_curve | Get season-best Power Duration Curve across recent activities. | activity_analysis | NO |
| get_pregnancy_summary | Get pregnancy summary data | womens_health | NO |
| get_primary_training_device | Get information about the primary training device | devices | NO |
| get_progress_summary_between_dates | Get progress summary for a metric between dates | training | NO |
| get_race_predictions | Get predicted race times based on current fitness level | challenges | SI |
| get_respiration_data | Get full respiration time-series data | health_wellness | NO |
| get_respiration_summary | Get respiration summary with essential metrics (lightweight version) | health_wellness | SI |
| get_respiration_trend | Get overnight respiration rate trend over a date range. | training | NO |
| get_rhr_day | Get resting heart rate data | health_wellness | SI |
| get_scheduled_workouts | Get scheduled workouts between two dates with curated summary list | workouts | NO |
| get_sleep_data | Get full sleep data with all details | health_wellness | SI |
| get_sleep_summary | Get sleep summary with only essential metrics (lightweight version) | health_wellness | SI |
| get_spo2_data | Get SpO2 (blood oxygen) data | health_wellness | SI |
| get_stats | Get daily activity stats with curated essential metrics | health_wellness | SI |
| get_stats_and_body | Get stats and body composition data | health_wellness | NO |
| get_steps_data | Get detailed steps data with 15-minute intervals | health_wellness | NO |
| get_stress_data | Get full stress time-series data | health_wellness | NO |
| get_stress_summary | Get stress summary with essential metrics (lightweight version) | health_wellness | SI |
| get_training_effect | Get training effect data for a specific activity | training | SI |
| get_training_load_trend | Get the Performance Management Chart (CTL/ATL/TSB) over a date range. | training | SI |
| get_training_plan_workouts | Get training plan workouts for the week containing the given date | workouts | NO |
| get_training_readiness | Get training readiness data with curated metrics | health_wellness | SI |
| get_training_status | Get training status with curated metrics | training | SI |
| get_unit_system | Get user's preferred unit system from profile | user_profile | NO |
| get_user_profile | Get user profile information | user_profile | SI |
| get_user_summary | Get user summary data (compatible with garminconnect-ha) | health_wellness | NO |
| get_userprofile_settings | Get user profile settings | user_profile | NO |
| get_vo2max_trend | Get VO2 max trend over a date range. | training | SI |
| get_weekly_intensity_minutes | Get weekly intensity minutes data aggregates | health_wellness | SI |
| get_weekly_steps | Get weekly step data aggregates | health_wellness | SI |
| get_weekly_stress | Get weekly stress data aggregates | health_wellness | SI |
| get_weigh_ins | Get weight measurements between specified dates | weight_management | NO |
| get_workout_by_id | Get detailed information for a specific workout | workouts | NO |
| get_workouts | Get all workouts with curated summary list | workouts | NO |
| log_custom_food | Log a custom food item to a meal on a date | nutrition | NO |
| log_food | Quick-add a food entry with macro values to the nutrition log | nutrition | NO |
| remove_gear_from_activity | Remove gear association from an activity | gear_management | NO |
| request_reload | Request reload of epoch data | training | NO |
| schedule_week | Schedule a list of workouts for the week in a single call. | workout_builders | NO |
| schedule_workout | Schedule a workout to a specific calendar date | workouts | NO |
| schedule_workouts | Schedule multiple workouts to specific calendar dates | workouts | NO |
| set_activity_name | Set or update the name of an activity. | activity_management | NO |
| set_blood_pressure | Set blood pressure values | data_management | NO |
| set_fit_download_dir | Set and persist the default directory for downloaded activity files. | activity_analysis | NO |
| unschedule_workout | Remove a scheduled workout from the Garmin Connect calendar | workouts | NO |
| unschedule_workouts | Remove multiple scheduled workouts from the Garmin Connect calendar | workouts | NO |
| update_custom_food | Update an existing custom food in the user's Garmin nutrition library | nutrition | NO |
| upload_course | Upload a GPX file as a Garmin Connect Course. | courses | NO |
| upload_workout | Upload a workout from JSON data | workouts | NO |
| upload_workouts | Upload multiple workouts from JSON data in a single call | workouts | NO |
| upsert_and_log | Find-or-create a custom food then log it in one step | nutrition | NO |