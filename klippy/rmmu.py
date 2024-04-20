from math import fabs
import re

class RMMU:

	#####
	# Initialize
	#####
	def __init__(self, config):
		# get klipper objects
		self.config = config
		self.name = config.get_name()
		self.printer = self.config.get_printer()
		self.reactor = self.printer.get_reactor()
		self.gcode = self.printer.lookup_object('gcode')

		# rmmu default status
		self.is_homed = False
		self.filament_changes = 0
		self.initial_tool = -1
		self.runout_detected = False
		self.needs_initial_purging = False
		self.spool_joins = []
		self.spool_mapping = []
		self.start_print_param = None
		self.toolhead_mapping = []

		# load config settings
		self.load_settings()

		# get manual_stepper rmmu_pulley endstop
		self.toolhead_sensor_endstop = None
		query_endstops = self.printer.load_object(self.config, 'query_endstops')
		for i in range(0, len(query_endstops.endstops)):
			if query_endstops.endstops[i][1] == "manual_stepper rmmu_pulley":
				self.toolhead_sensor_endstop = query_endstops.endstops[i][0]
				break
		if self.toolhead_sensor_endstop == None:
			raise self.config.error("RMMU Pulley endstop not configured! Please configure the toolhead filament sensor endstop.")

		# get additional endstops
		self.parking_sensor_endstop = None
		self.parking_t_sensor_endstop = []
		ppins = self.printer.lookup_object('pins')
		if self.parking_endstop_pin is not None:
			# ptfe adapter endstop
			mcu_endstop = ppins.setup_pin('endstop', self.parking_endstop_pin)
			self.parking_sensor_endstop = mcu_endstop
		elif len(self.parking_t_endstop_pin) == self.tool_count:
			# rmmu Tx endstops
			for i in range(0, self.tool_count):
				if self.parking_t_endstop_pin[i] is not None:
					mcu_endstop = ppins.setup_pin('endstop', self.parking_t_endstop_pin[i])
					self.parking_t_sensor_endstop.append(mcu_endstop)

		# register gcode commands
		self.register_commands()

		# register klipper handler
		self.register_handler()

	#####
	# Handler
	#####
	def register_handler(self):
		self.printer.register_event_handler("klippy:connect", self._connect)
		self.printer.register_event_handler("stepper_enable:motor_off", self._motor_off)

	def _connect(self):
		# get toolhead and extruder
		self.toolhead = self.printer.lookup_object('toolhead')
		self.extruder = self.printer.lookup_object('extruder')
		self.pause_resume = self.printer.lookup_object('pause_resume')
		self.v_sd = self.printer.lookup_object('virtual_sdcard', None)
		self.pheaters = self.printer.lookup_object('heaters')
		self.heater = self.extruder.get_heater()

		# get stepper
		self.rmmu_idler = self.printer.lookup_object("manual_stepper rmmu_idler", None)
		if self.rmmu_idler == None:
			raise self.config.error("RMMU Idler stepper not found!")
		self.rmmu_pulley = self.printer.lookup_object("manual_stepper rmmu_pulley", None)
		if self.rmmu_pulley == None:
			raise self.config.error("RMMU Pulley stepper not found!")

		# get toolhead filament sensor
		self.toolhead_filament_sensor_t0 = self.printer.lookup_object("filament_switch_sensor toolhead_filament_sensor_t0", None)
		if self.toolhead_filament_sensor_t0 == None:
			raise self.config.error("Toolhead filament sensor not found! Please configure the RatOS toolhead_filament_sensor_t0 filament sensor.")

		# get feeder filament sensors
		self.feeder_filament_sensors = []
		for i in range(0, self.tool_count):
			for filament_sensor in self.printer.lookup_objects('filament_switch_sensor'):
				sensor_name = filament_sensor[1].runout_helper.name
				if sensor_name == 'feeder_filament_sensor_t' + str(i):
					self.feeder_filament_sensors.append(filament_sensor[1])

	def _motor_off(self, print_time):
		self.reset()
		self.gcode.run_script_from_command('_LED_MOTORS_OFF')

	#####
	# G-Code Commands
	#####
	def register_commands(self):
		self.gcode.register_command('RMMU_HOME', self.cmd_RMMU_HOME, desc=(self.desc_RMMU_HOME))
		self.gcode.register_command('RMMU_RESET', self.cmd_RMMU_RESET, desc=(self.desc_RMMU_RESET))
		self.gcode.register_command('RMMU_LOAD_FILAMENT', self.cmd_RMMU_LOAD_FILAMENT, desc=(self.desc_RMMU_LOAD_FILAMENT))
		self.gcode.register_command('RMMU_MOVE_FILAMENT', self.cmd_RMMU_MOVE_FILAMENT, desc=(self.desc_RMMU_MOVE_FILAMENT))
		self.gcode.register_command('RMMU_UNLOAD_FILAMENT', self.cmd_RMMU_UNLOAD_FILAMENT, desc=(self.desc_RMMU_UNLOAD_FILAMENT))
		self.gcode.register_command('RMMU_EJECT_FILAMENT', self.cmd_RMMU_EJECT_FILAMENT, desc=(self.desc_RMMU_EJECT_FILAMENT))
		self.gcode.register_command('RMMU_CHANGE_FILAMENT', self.cmd_RMMU_CHANGE_FILAMENT, desc=(self.desc_RMMU_CHANGE_FILAMENT))
		self.gcode.register_command('RMMU_END_PRINT', self.cmd_RMMU_END_PRINT, desc=(self.desc_RMMU_END_PRINT))
		self.gcode.register_command('RMMU_START_PRINT', self.cmd_RMMU_START_PRINT, desc=(self.desc_RMMU_START_PRINT))
		self.gcode.register_command('RMMU_HOME_FILAMENT', self.cmd_RMMU_HOME_FILAMENT, desc=(self.desc_RMMU_HOME_FILAMENT))
		self.gcode.register_command('RMMU_TEST_FILAMENTS', self.cmd_RMMU_TEST_FILAMENTS, desc=(self.desc_RMMU_TEST_FILAMENTS))
		self.gcode.register_command('RMMU_FILAMENT_INSERT', self.cmd_RMMU_FILAMENT_INSERT, desc=(self.desc_RMMU_FILAMENT_INSERT))
		self.gcode.register_command('RMMU_FILAMENT_RUNOUT', self.cmd_RMMU_FILAMENT_RUNOUT, desc=(self.desc_RMMU_FILAMENT_RUNOUT))
		self.gcode.register_command('RMMU_CALIBRATE_REVERSE_BOWDEN', self.cmd_RMMU_CALIBRATE_REVERSE_BOWDEN, desc=(self.desc_RMMU_CALIBRATE_REVERSE_BOWDEN))
		self.gcode.register_command('RMMU_QUERY_SENSORS', self.cmd_RMMU_QUERY_SENSORS, desc=(self.desc_RMMU_QUERY_SENSORS))
		self.gcode.register_command('RMMU_JOIN_SPOOLS', self.cmd_RMMU_JOIN_SPOOLS, desc=(self.desc_RMMU_JOIN_SPOOLS))
		self.gcode.register_command('RMMU_REMAP_TOOLHEADS', self.cmd_RMMU_REMAP_TOOLHEADS, desc=(self.desc_RMMU_REMAP_TOOLHEADS))

	desc_RMMU_MOVE_FILAMENT = "Moves a filament with or without extruder sync."
	def cmd_RMMU_MOVE_FILAMENT(self, param):
		self.move_filament(param)

	desc_RMMU_LOAD_FILAMENT = "Loads a filament form its parking position into the hotend."
	def cmd_RMMU_LOAD_FILAMENT(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=0, maxval=self.tool_count)

		# check toolhead filament sensor
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			raise self.printer.command_error("Can not load filament! Another one is already loaded.")

		# home if needed
		if not self.is_homed:
			self.home()

		# load filament
		if not self.load_filament(tool):
			self.on_loading_error(tool)
			return
	
	desc_RMMU_UNLOAD_FILAMENT = "Unloads a filament from the hotend to its parking position."
	def cmd_RMMU_UNLOAD_FILAMENT(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=-1, maxval=self.tool_count)

		# check toolhead filament sensor
		if not self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			raise self.printer.command_error("No filament loaded!")

		# home if needed
		if not self.is_homed:
			self.home()

		# unload filament
		loaded_filament = self.get_setting(self.VARS_LOADED_FILAMENT)
		tool = tool if tool >- 1 else loaded_filament
		if tool >-1 and tool < self.tool_count:
			self.unload_filament(tool)
		else:
			raise self.printer.command_error("Can not unload unknown filament!")

		# release idler
		self.select_filament(-1)

	desc_RMMU_EJECT_FILAMENT = "Ejects one or all filament(s) from the RMMU device."
	def cmd_RMMU_EJECT_FILAMENT(self, param):
		tool = param.get_int('TOOLHEAD', None, minval=-1, maxval=self.tool_count)
		self.eject_filaments(tool)

	desc_RMMU_RESET = "Resets the RMMU device."
	def cmd_RMMU_RESET(self, param):
		self.reset()

	desc_RMMU_HOME = "Homes the RMMU idler."
	def cmd_RMMU_HOME(self, param):
		self.home()

	desc_RMMU_CHANGE_FILAMENT = "Called during the print to switch to another filament. Do not call it manually!"
	def cmd_RMMU_CHANGE_FILAMENT(self, param):
		self.change_filament(param)

	desc_RMMU_END_PRINT = "Called from the END_PRINT gcode macro. Unloads the filament and resets the RMMU device."
	def cmd_RMMU_END_PRINT(self, param):
		self.end_print()

	desc_RMMU_START_PRINT = "RMMU_START_PRINT gcode macro. Calls the RatOS START_PRINT macro if there are no errors."
	def cmd_RMMU_START_PRINT(self, param):
		self.start_print(param)

	desc_RMMU_TEST_FILAMENTS = "Tests if filaments, that are needed for the print, are available or not."
	def cmd_RMMU_TEST_FILAMENTS(self, param):
		self.test_filaments(param)

	desc_RMMU_HOME_FILAMENT = "Homes one or all filament(s) to their homing positions."
	def cmd_RMMU_HOME_FILAMENT(self, param):
		self.home_filaments(param)

	desc_RMMU_FILAMENT_INSERT = "Called from the RatOS feeder sensor insert detection."
	def cmd_RMMU_FILAMENT_INSERT(self, param):
		self.on_filament_insert(param)

	desc_RMMU_FILAMENT_RUNOUT = "Called from the RatOS feeder sensor runout detection."
	def cmd_RMMU_FILAMENT_RUNOUT(self, param):
		self.on_filament_runout(param)

	desc_RMMU_QUERY_SENSORS = "Queries all available RMMU sensors and endstops."
	def cmd_RMMU_QUERY_SENSORS(self, param):
		self.query_sensors()

	desc_RMMU_CALIBRATE_REVERSE_BOWDEN = "Auto detection of the reverse bowden length."
	def cmd_RMMU_CALIBRATE_REVERSE_BOWDEN(self, param):
		self.calibrate_reverse_bowden_length()

	desc_RMMU_JOIN_SPOOLS = "Configures the spool join feature."
	def cmd_RMMU_JOIN_SPOOLS(self, param):
		self.join_spools(param)

	desc_RMMU_REMAP_TOOLHEADS = "Configures the toolhead mapping feature."
	def cmd_RMMU_REMAP_TOOLHEADS(self, param):
		self.remap_toolhead(param)

	#####
	# Settings
	#####
	VARS_LOADED_FILAMENT = "rmmu_loaded_filament"
	VARS_LOADED_FILAMENT_TEMP = "rmmu_loaded_filament_temp"
	VARS_REVERSE_BOWDEN_LENGTH = "rmmu_reverse_bowden_length"

	def load_settings(self):
		# slicer profile settings
		self.travel_speed = 0
		self.travel_accel = 0
		self.wipe_accel = 0

		# mmu config
		self.tool_count = self.config.getint('tool_count', 4)
		self.reverse_bowden_length = self.get_setting(self.VARS_REVERSE_BOWDEN_LENGTH)
		if (self.reverse_bowden_length == 0):
			self.reverse_bowden_length = 500
		self.toolhead_sensor_to_extruder_gears_distance = self.config.getfloat('toolhead_sensor_to_extruder_gears_distance', 10.0)
		self.extruder_gears_to_cooling_zone_distance = self.config.getfloat('extruder_gears_to_cooling_zone_distance', 40.0)
		self.has_ptfe_adapter = True if self.config.get('has_ptfe_adapter', "false").lower() == "true" else False 
		self.make_extruder_test = True if self.config.get('make_extruder_test', "true").lower() == "true" else False 

		# endstop pins
		self.parking_endstop_pin = None
		self.parking_t_endstop_pin = []
		if self.config.get('parking_endstop_pin', None) is not None:
			# ptfe adapter endstop pins
			self.parking_endstop_pin = self.config.get('parking_endstop_pin')
		elif self.config.get('parking_t0_endstop_pin', None) is not None:
			# Tx endstop pins
			for i in range(0, self.tool_count):
				if self.config.get('parking_t' + str(i) + '_endstop_pin', None) is not None:
					self.parking_t_endstop_pin.append(self.config.get('parking_t' + str(i) + '_endstop_pin'))

		# idler config
		self.idler_positions = [102,76,50,24]
		self.idler_speed = self.config.getfloat('idler_speed', 300.0)
		self.idler_accel = self.config.getfloat('idler_accel', 3000.0)
		self.idler_home_position = self.config.getfloat('idler_home_position', 0)
		self.idler_homing_speed = self.config.getfloat('idler_homing_speed', 40)
		self.idler_homing_accel = self.config.getfloat('idler_homing_accel', 200)

		# filament homing config
		self.filament_homing_speed = self.config.getfloat('filament_homing_speed', 250.0)
		self.filament_homing_accel = self.config.getfloat('filament_homing_accel', 2000.0)
		self.filament_homing_parking_distance = self.config.getfloat('filament_homing_parking_distance', 50.0)
		self.filament_cleaning_distance = self.config.getfloat('filament_cleaning_distance', 100.0)

		# filament parking config
		self.filament_parking_speed = self.config.getfloat('filament_parking_speed', 300.0)
		self.filament_parking_accel = self.config.getfloat('filament_parking_accel', 2000.0)
		self.filament_parking_distance = self.config.getfloat('filament_parking_distance', 50.0)

		# filament cooling zone config
		self.cooling_zone_loading_speed = self.config.getfloat('cooling_zone_loading_speed', 30.0)
		self.cooling_zone_loading_accel = self.config.getfloat('cooling_zone_loading_accel', 500)
		self.cooling_zone_unloading_speed = self.config.getfloat('cooling_zone_unloading_speed', 50.0)
		self.cooling_zone_unloading_accel = self.config.getfloat('cooling_zone_unloading_accel', 1000)
		self.cooling_zone_unloading_pause = self.config.getfloat('cooling_zone_unloading_pause', 1000.0)
		self.cooling_zone_unloading_distance = self.config.getfloat('cooling_zone_unloading_distance', 130.0)

	def set_setting(self, variable, value):
		self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%s" % (variable, value))

	def get_setting(self, variable):
		return self.printer.lookup_object('save_variables').allVariables.get(variable, None)

	#####
	# Status
	#####
	def get_status(self, eventtime):
		return {'name': self.name,
		  'tool_count': self.tool_count,
		  'is_homed': self.is_homed,
		  'filament_changes': self.filament_changes,
		  'initial_tool': self.initial_tool,
		  'needs_initial_purging': self.needs_initial_purging,
		  'loaded_filament': self.get_setting(self.VARS_LOADED_FILAMENT),
		  'loaded_filament_temp': self.get_setting(self.VARS_LOADED_FILAMENT_TEMP)}

	def reset(self):
		# default values
		self.is_homed = False
		self.filament_changes = 0
		self.runout_detected = False
		self.start_print_param = None
		self.needs_initial_purging = False

		# update frontend
		loaded_filament = self.get_setting(self.VARS_LOADED_FILAMENT)
		for i in range(0, self.tool_count):
			if i == loaded_filament:
				self.gcode.run_script_from_command("SET_GCODE_VARIABLE MACRO=T" + str(i) + " VARIABLE=active VALUE=True")
				self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "00FF00" + "\"\'")
			else:
				self.gcode.run_script_from_command("SET_GCODE_VARIABLE MACRO=T" + str(i) + " VARIABLE=active VALUE=False")
				self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FFFF00" + "\"\'")

	#####
	# Start / End Print
	#####
	def start_print(self, param):
		# parameter
		self.initial_tool = param.get_int('INITIAL_TOOL', None, minval=0, maxval=self.tool_count)
		self.travel_speed = param.get_int('TRAVEL_SPEED', None, minval=0, maxval=1000)
		self.travel_accel = param.get_int('TRAVEL_ACCEL', None, minval=0, maxval=100000)
		self.wipe_accel = param.get_int('WIPE_ACCEL', None, minval=0, maxval=100000)
		self.start_print_param = param

		# handle toolhead mapping
		self.initial_tool = self.get_remapped_toolhead(self.initial_tool)

		# home if needed
		if not self.is_homed:
			self.home()

		# check for filament in hotend
		self.filament_changes = 0
		self.needs_initial_purging = True
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			loaded_filament = self.get_setting(self.VARS_LOADED_FILAMENT)
			loaded_filament_temp = self.get_setting(self.VARS_LOADED_FILAMENT_TEMP)
			if loaded_filament >=0 and loaded_filament <= self.tool_count:
				if loaded_filament != self.initial_tool:
					if loaded_filament_temp > self.heater.min_extrude_temp and loaded_filament_temp < self.heater.max_temp:
						# unloaded the filament that is already loaded
						self.ratos_echo("Wrong filament detected in hotend!")
						self.ratos_echo("Unloading filament T" + str(loaded_filament) + "! Please wait...")

						# start heating up extruder but dont wait for it so we can save some time
						self.ratos_echo("Preheating extruder to " + str(loaded_filament_temp) + "°C.")
						self.extruder_set_temperature(loaded_filament_temp, False)

						# home printer if needed and move toolhead to its parking position
						self.gcode.run_script_from_command('MAYBE_HOME')
						self.gcode.run_script_from_command('_MOVE_TO_LOADING_POSITION TOOLHEAD=0')

						# wait for the extruder to heat up
						self.ratos_echo("Heating up extruder to " + str(loaded_filament_temp) + "°C! Please wait...")
						self.extruder_set_temperature(loaded_filament_temp, True)					

						# unload filament
						if not self.unload_filament(loaded_filament):
							self.extruder_set_temperature(0, False)					
							raise self.printer.command_error("Could not unload filament! Please unload the filament and restart the print.")

						# cool down extruder, dont wait for it
						self.extruder_set_temperature(0, False)					
					else:
						raise self.printer.command_error("Unknown filament detected in toolhead! Please unload the filament and restart the print.")
				else:
					# tell RatOS that initial purging is not needed
					self.needs_initial_purging = False
			else:
				raise self.printer.command_error("Unknown filament detected in toolhead! Please unload the filament and restart the print.")

		# test if all demanded filaments are available and raises an error if not
		self.test_filaments(param)

		# disable toolhead filament sensor
		self.toolhead_filament_sensor_t0.runout_helper.sensor_enabled = False

		# call RatOS start print gcode macro
		self.gcode.run_script_from_command('START_PRINT ' + str(param.get_raw_command_parameters().strip()))

	def end_print(self):
		# reset rmmu
		self.reset()

		# reset spool join and toolhead mapping
		self.spool_joins = []
		self.spool_mapping = []
		self.toolhead_mapping = []

	#####
	# Home
	#####
	def home(self):
		self.ratos_echo("Homing RMMU...")
		self.reset()
		self.home_idler()
		self.is_homed = True
		self.ratos_echo("Hello RMMU!")

	def home_idler(self):
		self.rmmu_pulley.do_set_position(0.0)
		self.rmmu_idler.do_set_position(0.0)
		self.stepper_move(self.rmmu_idler, 2, True, self.idler_homing_speed, self.idler_homing_accel)
		self.stepper_homing_move(self.rmmu_idler, -300, self.idler_homing_speed, self.idler_homing_accel, 1)
		self.rmmu_idler.do_set_position(-1.0)
		self.stepper_move(self.rmmu_idler, self.idler_home_position, True, self.idler_homing_speed, self.idler_homing_accel)

	def home_filaments(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=-1, maxval=self.tool_count)

		# check for filament in hotend
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			raise self.printer.command_error("Can not home filaments! Filament in hotend detected.")

		# update frontend
		for i in range(0, self.tool_count):
			if tool == -1 or tool == i:
				self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FFFF00" + "\"\'")

		# home if needed
		if not self.is_homed:
			self.home()

		# home filaments
		for i in range(0, self.tool_count):
			if tool == -1 or tool == i:

				# home filament
				if self.home_filament(i):
					self.ratos_echo("Filament T" + str(i) + " homed!")
					self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "00FF00" + "\"\'")
				else:
					self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FF0000" + "\"\'")
					self.ratos_echo("Could not home filament T" + str(i) + "! Filament homing stopped.")
					break

				# check parking sensor
				if self.parking_sensor_endstop != None:
					if self.is_endstop_triggered(self.parking_sensor_endstop):
						self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FF0000" + "\"\'")
						self.ratos_echo("Parking filament sensor isssue detected! Filament homing stopped.")
						self.select_filament(i)
						self.rmmu_pulley.do_set_position(0.0)
						self.stepper_move(self.rmmu_pulley, -100, True, 100, 500)
						break

				# check Tx parking sensor
				elif self.has_ptfe_adapter and len(self.parking_t_sensor_endstop) == self.tool_count:
					if not self.is_endstop_triggered(self.parking_t_sensor_endstop[i]):
						self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FF0000" + "\"\'")
						self.ratos_echo("Parking filament sensor isssue detected! Filament homing stopped.")
						self.select_filament(i)
						self.rmmu_pulley.do_set_position(0.0)
						self.stepper_move(self.rmmu_pulley, -100, True, 100, 500)
						break

				# check toolhead sensor
				else:
					if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
						self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=T' + str(i) + ' VARIABLE=color VALUE=\'"' + "FF0000" + "\"\'")
						self.ratos_echo("Toolhead filament sensor isssue detected! Filament homing stopped.")
						self.select_filament(i)
						self.rmmu_pulley.do_set_position(0.0)
						self.stepper_move(self.rmmu_pulley, -100, True, 100, 500)
						break

		# release idler
		self.select_filament(-1)

	def home_filament(self, filament):
		# echo
		self.ratos_echo("Homing filament T" + str(filament) + "...")

		# select filament
		self.select_filament(filament)

		# home filament
		if self.parking_sensor_endstop != None:
			if not self.load_filament_from_parking_position_to_parking_sensor(filament):
				return False
			if not self.unload_filament_from_parking_sensor_to_parking_position(filament):
				return False
		elif self.has_ptfe_adapter and len(self.parking_t_sensor_endstop) == self.tool_count:
			if not self.load_filament_from_parking_position_to_tx_parking_sensor(filament):
				return False
			if not self.unload_filament_from_tx_parking_sensor_to_parking_position(filament):
				return False
		else:
			if not self.load_filament_from_reverse_bowden_to_toolhead_sensor(filament):
				return False
			if not self.unload_filament_from_toolhead_sensor_to_reverse_bowden(filament):
				return False

		# echo
		self.ratos_echo("Filament T" + str(filament) + " homed!")

		# success
		return True

	#####
	# Change Filament
	#####
	def change_filament(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=0, maxval=self.tool_count)
		x = param.get_float('X', None, minval=-1, maxval=999)
		y = param.get_float('Y', None, minval=-1, maxval=999)

		# handle toolhead mapping
		tool = self.get_remapped_toolhead(tool)

		# handle spool mapping
		tool = self.get_remapped_spool(tool)

		# we ignore the first filament change since we have already loaded the first filament during the start print macro
		if self.filament_changes > 0:
			# run before filament change gcode macro
			self.gcode.run_script_from_command('_RMMU_BEFORE_FILAMENT_CHANGE TOOLHEAD=' + str(tool) + ' X=' + str(x) + ' Y=' + str(y) + ' TRAVEL_SPEED=' + str(self.travel_speed) + ' TRAVEL_ACCEL=' + str(self.travel_accel) + ' WIPE_ACCEL=' + str(self.wipe_accel))

			# enable toolhead filament sensor
			self.toolhead_filament_sensor_t0.runout_helper.sensor_enabled = True

			# check toolhead filament sensor
			if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
				# unload filament
				loaded_filament = self.get_setting(self.VARS_LOADED_FILAMENT)
				if not self.unload_filament(loaded_filament, "change_filament"):
					self.select_filament(-1)
					self.ratos_echo("Could not unload filament T" + str(loaded_filament) + "!")
					return
			else:
				# toolhead filament sensor false state detected
				self.ratos_echo("Possible sensor failure! Filament sensor should be triggered but it isnt.")
				return

			# load filament
			if not self.load_filament(tool):
				self.on_loading_error(tool)

			# disable toolhead filament sensor
			self.toolhead_filament_sensor_t0.runout_helper.sensor_enabled = False

		# update filament change counter
		self.filament_changes = self.filament_changes + 1

	def load_filament(self, tool):
		# echo
		self.ratos_echo("Loading filament T" + str(tool) + "...")

		# home if not homed yet
		if not self.is_homed:
			self.home()

		# reset loaded filament
		self.set_setting(self.VARS_LOADED_FILAMENT, -1)
		self.set_setting(self.VARS_LOADED_FILAMENT_TEMP, -1)

		# select filament
		self.select_filament(tool)

		# load filament to toolhead sensor
		if self.parking_sensor_endstop != None:
			if not self.load_filament_from_parking_position_to_parking_sensor(tool):
				return False
			if not self.load_filament_from_parking_sensor_to_toolhead_sensor(tool):
				return False
		elif self.has_ptfe_adapter and len(self.parking_t_sensor_endstop) == self.tool_count:
			if not self.load_filament_from_parking_sensor_to_toolhead_sensor(tool):
				return False
		else:
			if not self.load_filament_from_reverse_bowden_to_toolhead_sensor(tool):
				self.ratos_echo("Could not load filament T" + str(tool) + " into toolhead filament sensor!")
				return False

		# extruder test
		if self.make_extruder_test:
			if not self.extruder_test(tool):
				return False
		else:
			# move filament into cooling zone
			self.stepper_synced_move(self.extruder_gears_to_cooling_zone_distance + self.toolhead_sensor_to_extruder_gears_distance, self.cooling_zone_loading_speed, self.cooling_zone_loading_accel)
			# release idler
			self.select_filament(-1)

		# load filament into hotend cooling zone
		self.gcode.run_script_from_command('_LOAD_FILAMENT_FROM_COOLING_ZONE_TO_NOZZLE TOOLHEAD=0 PURGE=False')

		# update frontend
		for i in range(0, self.tool_count):
			if tool == i:
				self.gcode.run_script_from_command("SET_GCODE_VARIABLE MACRO=T" + str(i) + " VARIABLE=active VALUE=True")
			else:
				self.gcode.run_script_from_command("SET_GCODE_VARIABLE MACRO=T" + str(i) + " VARIABLE=active VALUE=False")

		# send notification
		if self.filament_changes > 0:
			self.gcode.run_script_from_command('_RMMU_ON_FILAMENT_HAS_CHANGED TOOLHEAD=' + str(tool))

		# reset runout detection
		self.runout_detected = False

		# set loaded filament
		self.set_setting(self.VARS_LOADED_FILAMENT, tool)
		self.set_setting(self.VARS_LOADED_FILAMENT_TEMP, self.extruder_get_target_temperature())

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded.")

		# success 
		return True

	def unload_filament(self, tool, origin = ""):
		# echo
		self.ratos_echo("Unloading filament T" + str(tool) + "...")

		# unload filament 
		if origin == "change_filament":
			self.gcode.run_script_from_command('_RMMU_UNLOAD_FILAMENT_FROM_NOZZLE_TO_COOLING_ZONE TOOLHEAD=' + str(tool) + ' PAUSE=' + str(self.cooling_zone_unloading_pause))
		else:
			self.gcode.run_script_from_command('_UNLOAD_FILAMENT_FROM_NOZZLE_TO_COOLING_ZONE TOOLHEAD=0')

		# select filament 
		self.select_filament(tool)

		# unload filament from cooling zone to reverse bowden 
		if not self.unload_filament_from_cooling_zone_to_reverse_bowden(tool):
			return False

		# park filament 
		if self.parking_sensor_endstop != None:
			if not self.unload_filament_from_reverse_bowden_to_parking_sensor(tool):
				return False
			if not self.unload_filament_from_parking_sensor_to_parking_position(tool):
				return False
		elif self.has_ptfe_adapter and len(self.parking_t_sensor_endstop) == self.tool_count:
			if not self.unload_filament_from_reverse_bowden_to_parking_sensor(tool):
				return False
			if not self.unload_filament_from_tx_parking_sensor_to_parking_position(tool):
				return False

		# update frontend 
		self.gcode.run_script_from_command("SET_GCODE_VARIABLE MACRO=T" + str(tool) + " VARIABLE=active VALUE=False")

		# reset loaded filament
		self.set_setting(self.VARS_LOADED_FILAMENT, -1)
		self.set_setting(self.VARS_LOADED_FILAMENT_TEMP, -1)

		# echo
		self.ratos_echo("Filament T" + str(tool) + " unloaded!")

		# success 
		return True

	#####
	# Select Filament
	#####
	def select_filament(self, tool=-1):
		# home if needed
		if not self.is_homed:
			self.home()

		# select idler
		if tool >= 0:
			self.stepper_move(self.rmmu_idler, self.idler_positions[tool], True, self.idler_speed, self.idler_accel)
		else:
			self.stepper_move(self.rmmu_idler, self.idler_home_position, True, self.idler_speed, self.idler_accel)

	#####
	# Load Filament
	#####
	def load_filament_from_parking_position_to_parking_sensor(self, tool):
		# echo
		self.ratos_echo("Loading filament T" + str(tool) + " from parking position into parking sensor...")

		# enable parking sensor endstop
		self.set_pulley_endstop(self.parking_sensor_endstop)

		# homing move
		max_step_count = 5
		if not self.is_endstop_triggered(self.parking_sensor_endstop):
			for i in range(max_step_count):
				self.stepper_homing_move(self.rmmu_pulley, self.filament_parking_distance + 10, self.filament_homing_speed, self.filament_homing_accel, 2)
				if self.is_endstop_triggered(self.parking_sensor_endstop):
					break

		# check sensor and try to fix issues if needed
		if not self.is_endstop_triggered(self.parking_sensor_endstop):
			self.ratos_echo("Could not load filament T" + str(tool) + " into parking sensor!")
			try_count = 5
			move_distance = 30
			for i in range(1, try_count):
				self.ratos_echo("Retry " + str(i) + " ...")
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, move_distance, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				if self.is_endstop_triggered(self.parking_sensor_endstop):
					self.ratos_echo("Problem solved!")
					break

		# check sensor
		if not self.is_endstop_triggered(self.parking_sensor_endstop):
			self.ratos_echo("Could not load filament T" + str(tool) + " into parking sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded into parking sensor!")

		# success
		return True

	def load_filament_from_parking_position_to_tx_parking_sensor(self, tool):
		# echo
		self.ratos_echo("Loading filament T" + str(tool) + " from parking position into parking sensor...")

		# enable parking sensor endstop
		self.set_pulley_endstop(self.parking_t_sensor_endstop[tool])

		# homing move
		max_step_count = 5
		if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			for i in range(max_step_count):
				self.stepper_homing_move(self.rmmu_pulley, -(self.filament_parking_distance + 10), self.filament_homing_speed, self.filament_homing_accel, -2)
				if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
					break

		# check sensor and try to fix issues if needed
		if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Could not load filament T" + str(tool) + " into parking sensor!")
			try_count = 5
			move_distance = 30
			for i in range(1, try_count):
				self.ratos_echo("Retry " + str(i) + " ...")
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, -move_distance, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
					self.ratos_echo("Problem solved!")
					break

		# check sensor
		if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Could not load filament T" + str(tool) + " into parking sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded into parking sensor!")

		# success
		return True

	def load_filament_from_parking_sensor_to_toolhead_sensor(self, tool):
		# echo
		self.ratos_echo("Loading filament T" + str(tool) + " from parking sensor to parking position...")

		# enable toolhead sensor endstop
		self.set_pulley_endstop(self.toolhead_sensor_endstop)

		# long homing move to toolhead sensor
		if not self.is_endstop_triggered(self.toolhead_sensor_endstop):
			self.stepper_homing_move(self.rmmu_pulley, self.reverse_bowden_length + 50, self.filament_homing_speed, self.filament_homing_accel, 2)

		# short homing moves in case long one wasnt successfull
		if not self.is_endstop_triggered(self.toolhead_sensor_endstop):
			step_distance = 50
			max_step_count = 5
			if not self.is_endstop_triggered(self.toolhead_sensor_endstop):
				for i in range(max_step_count):
					self.rmmu_pulley.do_set_position(0.0)
					self.stepper_homing_move(self.rmmu_pulley, step_distance, self.filament_homing_speed, self.filament_homing_accel, 2)
					if self.is_endstop_triggered(self.toolhead_sensor_endstop):
						break

		# check sensor
		if not self.is_endstop_triggered(self.toolhead_sensor_endstop):
			self.ratos_echo("Could not load filament T" + str(tool) + " into parking sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded to parking position!")

		# success
		return True

	def load_filament_from_reverse_bowden_to_toolhead_sensor(self, tool):
		# echo
		self.ratos_echo("Loading filament T" + str(tool) + " from reverse bowden into toolhead sensor...")

		# enable toolhead sensor endstop
		self.set_pulley_endstop(self.toolhead_sensor_endstop)

		# load filament into toolhead sensor
		step_distance = 100
		max_step_count = int((self.reverse_bowden_length * 1.5) / step_distance)
		if not self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			for i in range(max_step_count):
				self.stepper_homing_move(self.rmmu_pulley, step_distance, self.filament_homing_speed, self.filament_homing_accel, 2)
				if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
					break

		# check sensor
		if not self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			self.ratos_echo("Could not find toolhead filament sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded into toolhead sensor!")

		# success
		return True

	def extruder_test(self, tool):
		# echo
		self.ratos_echo("Extruder test with filament T" + str(tool) + "...")

		# extruder test
		for i in range(1, 5):
			if not self.push_and_pull_test(self.cooling_zone_loading_speed / i, self.cooling_zone_loading_accel / i):

				# test failed, lets pull the filament a bit out and try again
				self.stepper_synced_move(-100, self.cooling_zone_loading_speed / i, self.cooling_zone_loading_accel / i)

				# bring filament back to the toolhead sensor
				if not self.load_filament_from_reverse_bowden_to_toolhead_sensor(tool):

					# can not find the toolhead sensor, retract the filament a bit 
					self.stepper_synced_move(-100, self.cooling_zone_loading_speed / i, self.cooling_zone_loading_accel / i)

					# release idler
					self.select_filament(-1)

					# return error
					return False
			else:

				# test successful, move filament back into the cooling zone
				self.stepper_synced_move(self.extruder_gears_to_cooling_zone_distance + self.toolhead_sensor_to_extruder_gears_distance / 2, self.cooling_zone_loading_speed / i, self.cooling_zone_loading_accel / i)

				# release idler
				self.select_filament(-1)

				# echo
				self.ratos_echo("Extruder test successful!")

				# sucess
				return True

		# test failed, retract the filament a bit 
		self.stepper_synced_move(-100, self.cooling_zone_loading_speed, self.cooling_zone_loading_accel)

		# release idler
		self.select_filament(-1)

		# return error
		return False

	def push_and_pull_test(self, loading_speed, loading_accel):
		# echo
		self.ratos_echo("Push and pull test...")

		# try to move filament into cooling zone
		self.stepper_synced_move(self.extruder_gears_to_cooling_zone_distance + self.toolhead_sensor_to_extruder_gears_distance, loading_speed, loading_accel)

		# retract the filament and stop before it hits the toolhead sensor
		self.stepper_synced_move(-(self.extruder_gears_to_cooling_zone_distance + self.toolhead_sensor_to_extruder_gears_distance / 2), loading_speed, loading_accel)

		# return result
		return self.is_sensor_triggered(self.toolhead_filament_sensor_t0)

	#####
	# Unload Filament
	#####
	def unload_filament_from_reverse_bowden_to_parking_sensor(self, tool):
		# echo
		self.ratos_echo("Unloading filament T" + str(tool) + " from reverse bowden to parking sensor...")

		# handle sensor setup
		if self.parking_sensor_endstop != None:
			endstop = self.parking_sensor_endstop
		elif len(self.parking_t_sensor_endstop) == self.tool_count:
			endstop = self.parking_t_sensor_endstop[tool]
		self.set_pulley_endstop(endstop)

		# long homing move to parking sensor
		self.stepper_homing_move(self.rmmu_pulley, -(self.reverse_bowden_length + 50), self.filament_homing_speed, self.filament_homing_accel, -2)

		# short homing moves in case long one wasnt successfull
		if not self.is_endstop_triggered(endstop):
			step_distance = 50
			max_step_count = 5
			if self.is_endstop_triggered(endstop):
				for i in range(max_step_count):
					self.rmmu_pulley.do_set_position(0.0)
					self.stepper_homing_move(self.rmmu_pulley, step_distance, self.filament_homing_speed, self.filament_homing_accel, -2)
					if not self.is_endstop_triggered(endstop):
						break

		# check sensor and try to fix issues if needed
		if self.is_endstop_triggered(endstop):
			self.ratos_echo("Could not unload filament T" + str(tool) + " to parking sensor!")
			try_count = 5
			move_distance = 50
			for i in range(1, try_count):
				self.ratos_echo("Retry " + str(i) + " ...")
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, 10, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, -move_distance, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				if not self.is_endstop_triggered(endstop):
					self.ratos_echo("Problem solved!")
					break

		# check sensor
		if self.is_endstop_triggered(endstop):
			self.ratos_echo("Could not unload filament T" + str(tool) + " to parking sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " loaded into parking sensor!")

		# success
		return True

	def unload_filament_from_parking_sensor_to_parking_position(self, tool):
		# park filament
		self.rmmu_pulley.do_set_position(0.0)
		self.stepper_move(self.rmmu_pulley, -self.filament_parking_distance, True, self.filament_homing_speed, self.filament_homing_accel)

		# check sensor and try to fix issues if needed
		if self.is_endstop_triggered(self.parking_sensor_endstop):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from parking sensor!")
			try_count = 5
			move_distance = 50
			for i in range(1, try_count):
				self.ratos_echo("Retry " + str(i) + " ...")
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, 10, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, -move_distance, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				if not self.is_endstop_triggered(self.parking_sensor_endstop):
					self.ratos_echo("Problem solved!")
					break

		# check sensor
		if self.is_endstop_triggered(self.parking_sensor_endstop):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from parking sensor!")
			return False

		# success
		return True

	def unload_filament_from_tx_parking_sensor_to_parking_position(self, tool):
		# park filament
		self.rmmu_pulley.do_set_position(0.0)
		self.stepper_move(self.rmmu_pulley, self.filament_parking_distance, True, self.filament_homing_speed, self.filament_homing_accel)

		# check sensor and try to fix issues if needed
		if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from parking sensor!")
			try_count = 5
			move_distance = 50
			for i in range(1, try_count):
				self.ratos_echo("Retry " + str(i) + " ...")
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, -10, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				self.rmmu_pulley.do_set_position(0.0)
				self.stepper_move(self.rmmu_pulley, move_distance, True, self.filament_homing_speed / i, self.filament_homing_accel / i)
				if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
					self.ratos_echo("Problem solved!")
					break

		# check sensor
		if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from parking sensor!")
			return False

		# success
		return True

	def unload_filament_from_cooling_zone_to_reverse_bowden(self, tool):
		# echo
		self.ratos_echo("Unloading filament T" + str(tool) + " from cooling zone to reverse bowden...")

		# unload filament from cooling zone to reverse bowden
		self.stepper_synced_move(-(self.cooling_zone_unloading_distance), self.cooling_zone_unloading_speed, self.cooling_zone_unloading_accel)

		# check sensor
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from cooling zone to reverse bowden!")
			if self.filament_cleaning_distance > 0:
				self.ratos_echo("Trying to clean the toolhead filament sensor...")
				self.stepper_move(self.rmmu_pulley, self.filament_cleaning_distance, True, self.filament_homing_speed, self.filament_homing_accel)
				self.stepper_move(self.rmmu_pulley, -(self.filament_cleaning_distance * 2), True, self.filament_homing_speed, self.filament_homing_accel)
				if not self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
					self.ratos_echo("Toolhead filament sensor successfully cleaned!")
					return True
				self.ratos_echo("Could not clean toolhead filament sensor!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " unloaded to reverse bowden!")

		# success
		return True

	def unload_filament_from_toolhead_sensor_to_reverse_bowden(self, tool):
		# echo
		self.ratos_echo("Unloading filament T" + str(tool) + " from toolhead sensor to reverse bowden...")

		# unload filament to reverse bowden
		self.select_filament(tool)
		self.rmmu_pulley.do_set_position(0.0)
		self.stepper_move(self.rmmu_pulley, -self.filament_homing_parking_distance, True, self.filament_homing_speed, self.filament_homing_accel)

		# check sensor
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			self.ratos_echo("Could not unload filament T" + str(tool) + " from toolhead sensor to reverse bowden!")
			return False

		# echo
		self.ratos_echo("Filament T" + str(tool) + " unloaded to reverse bowden!")

		# success
		return True

	#####
	# Filament presence check
 	#####
	def test_filaments(self, param):
		# echo
		self.ratos_echo("Testing needed filaments...")

		# home if needed
		if not self.is_homed and len(self.parking_t_sensor_endstop) != self.tool_count:
			self.home()

		# test filaments
		for i in range(0, self.tool_count):
			toolhead_used = param.get('T' + str(i), "true") 
			if toolhead_used == "true":
				toolhead = self.get_remapped_toolhead(i)
				if not self.test_filament(toolhead):
					self.select_filament(-1)
					raise self.printer.command_error("Can not start print because Filament T" + str(toolhead) + " is not available!")

		# release idler
		if len(self.parking_t_sensor_endstop) != self.tool_count:
			self.select_filament(-1)

		# echo
		self.ratos_echo("All needed filaments available!")

		# testing spool join
		if len(self.spool_joins) > 0:
			self.ratos_echo("Validating spool join...")
			for spool_join in self.spool_joins:
				counter = 0
				for spool in spool_join:
					for i in range(0, self.tool_count):
						if param.get('T' + str(i), "true") == "true":
							if spool == i:
								counter += 1
				if counter > 1:
					raise self.printer.command_error("Can not start print because joined spools are part of the print!")
			self.ratos_echo("Spool join validated!")

	def test_filament(self, filament):
		# echo
		self.ratos_echo("Testing filament T" + str(filament) + "...")

		# test filament
		if len(self.parking_t_sensor_endstop) == self.tool_count or len(self.feeder_filament_sensors) == self.tool_count:
			if len(self.parking_t_sensor_endstop) == self.tool_count:
				if not self.is_endstop_triggered(self.parking_t_sensor_endstop[filament]):
					self.ratos_echo("Filament T" + str(filament) + " not detected!")
					return False
			if len(self.feeder_filament_sensors) == self.tool_count:
				if not self.is_sensor_triggered(self.feeder_filament_sensors[filament]):
					self.ratos_echo("Filament T" + str(filament) + " runout detected!")
					return False
			return True
		else:
			return self.home_filament(filament)

	#####
	# Eject Filament
	#####
	def eject_filaments(self, tool):
		# home if needed
		if not self.is_homed:
			self.home()

		# eject filaments
		for i in range(0, self.tool_count):
			if tool == -1 or tool == i:
				self.eject_filament(i)

		# release filament
		self.select_filament(-1)

	def eject_filament(self, tool):
		# echo
		self.ratos_echo("Ejecting filament T" + str(tool) + "...")

		# select filament
		self.select_filament(tool)

		if len(self.feeder_filament_sensors) == self.tool_count:
			# check filament runout sensor
			if not self.is_sensor_triggered(self.feeder_filament_sensors[tool]):
				self.ratos_echo("Filament T" + str(tool) + " already ejected!")
				return

		if len(self.parking_t_sensor_endstop) == self.tool_count:
			# check if filament is loaded
			if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
				self.ratos_echo("Filament T" + str(tool) + " already ejected!")
				return

			# enable parking sensor endstop
			self.set_pulley_endstop(self.parking_t_sensor_endstop[tool])

			# homing moves
			if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
				step_distance = 100
				max_step_count = int((self.reverse_bowden_length * 1.5) / step_distance)
				for i in range(max_step_count):
					self.stepper_homing_move(self.rmmu_pulley, -step_distance, self.filament_homing_speed, self.filament_homing_accel, -2)
					if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
						break

			# check sensor
			if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
				self.ratos_echo("Could not eject filament T" + str(tool) + "! Parking sensor still triggered.")
				return

			# eject filament from device
			self.rmmu_pulley.do_set_position(0.0)
			self.stepper_move(self.rmmu_pulley, -250, True, self.filament_homing_speed, self.filament_homing_accel)
			return

		# eject filament
		self.rmmu_pulley.do_set_position(0.0)
		self.stepper_move(self.rmmu_pulley, -(self.reverse_bowden_length * 1.5), True, self.filament_homing_speed, self.filament_homing_accel)

		# echo
		self.ratos_echo("Filament T" + str(tool) + " ejected!")

	#####
	# Join filament 
	#####
	def join_spools(self, param):
		# parameter
		parameter = param.get('SPOOLS', "").strip().replace(" ", "")

		# reset spool join
		if parameter == "":
			self.spool_joins = []
			self.echo_spool_join()
			return
			
		# check parameter format
		if "," not in parameter:
			self.ratos_echo("Wrong parameter!")
			return

		# get new spool join
		new_spools = [int(item) for item in parameter.split(',')]

		# check parameter
		if len(new_spools) < 2 or len(new_spools) > self.tool_count:
			self.ratos_echo("Wrong spool count!")
			return

		# check if new spools are already part of another spool join config
		if len(self.spool_joins) > 0:
			for spool_join in self.spool_joins:
				for joined_spool in spool_join:
					for new_spool in new_spools:
						if joined_spool == new_spool:
							self.ratos_echo("Spool T" + str(new_spool) + " already joined with another one!")
							self.echo_spool_join()
							return

		# check if new spools are being used in a ongoing print
		if self.start_print_param != None:
			counter = 0
			for spool in new_spools:
				for i in range(0, self.tool_count):
					if self.start_print_param.get('T' + str(i), "true") == "true":
						if spool == i:
							counter += 1
			if counter > 1:
				self.ratos_echo("Can not join spools because selected spools are part of the ongoing print!")
				return

		# add new spool join
		self.spool_joins.append(new_spools)
		self.echo_spool_join()

	def join_spool(self, spool_join, tool):
		for spool in spool_join:
			if spool != tool:
				if self.test_filament(spool):
					if self.load_filament(spool):
						spool_map_exists = False
						if len(self.spool_mapping) > 0:
							for spool_map in self.spool_mapping:
								if tool in spool_map:
									spool_map[tool] = spool
									spool_map_exists = True
						if not spool_map_exists:
							self.spool_mapping.append({tool: spool})
						return True
					else:
						self.ratos_echo("Can not join spool T" + str(spool) + "!")
						self.select_filament(tool)
						self.stepper_synced_move(-100, 50, 200)
						self.select_filament(-1)
						if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
							self.ratos_echo("Can not join spools! Toolhead sensor is still triggered after a unsuccessful filament loading.")
							return False
				else:
					self.ratos_echo("Spool " + str(spool) + " not available!")
		return False

	def get_remapped_spool(self, tool):
		if len(self.spool_mapping) > 0:
			for spool_map in self.spool_mapping:
				if tool in spool_map:
					tool = spool_map[tool]
					break
		return tool

	def echo_spool_join(self):
		if len(self.spool_joins) > 0:
			result = "Joining filament:\n" 
			for spool_join in self.spool_joins:
				result += "Spools: " + ",".join(str(i) for i in spool_join) + "\n"
			self.gcode.respond_raw(result)
			return
		result = "Spool joining deactivated!\n\n"
		result += "Deactivate joining with:\n"
		result += "JOIN_SPOOLS SPOOLS=\n\n"
		result += "Join spool 1, 2 and 3 with:\n"
		result += "JOIN_SPOOLS SPOOLS=1,2,3\n\n"
		result += "Join spool 1 and 2 and then 0 and 3 with:\n"
		result += "JOIN_SPOOLS SPOOLS=1,2\n"
		result += "JOIN_SPOOLS SPOOLS=0,3\n"
		self.gcode.respond_raw(result)

	#####
	# Toolhead mapping 
	#####
	def remap_toolhead(self, param):
		# parameter
		parameter = param.get('TOOLHEADS', "").strip().replace(" ", "")

		# check for ongoing print
		if self.start_print_param != None:
			self.ratos_echo("Toolhead remapping is not supported during a print!")
			return

		# reset mapping
		if parameter == "":
			self.toolhead_mapping = []
			self.echo_toolhead_mapping()
			return

		# check parameter format
		if "," not in parameter:
			self.ratos_echo("Wrong parameter!")
			return

		# get new mapping
		new_toolhead_map = [int(item) for item in parameter.split(',')]

		# check parameter count
		if len(new_toolhead_map) != 2:
			self.ratos_echo("Wrong toolhead count!")
			return

		# check new mapping
		if len(self.toolhead_mapping) > 0:
			for toolhead_map in self.toolhead_mapping:
				for toolhead in toolhead_map:
					for new_toolhead in new_toolhead_map:
						if toolhead == new_toolhead:
							self.ratos_echo("Can not remap toolhead T" + str(new_toolhead) + "! Toolhead is already mapped.")
							self.echo_toolhead_mapping()
							return

		# add new toolhead mapping
		self.toolhead_mapping.append(new_toolhead_map)
		self.echo_toolhead_mapping()

	def get_remapped_toolhead(self, tool):
		if len(self.toolhead_mapping) > 0:
			for toolhead_map in self.toolhead_mapping:
				if tool in toolhead_map:
					for t in toolhead_map:
						if tool != t:
							tool = t
							break
		return tool

	def echo_toolhead_mapping(self):
		if len(self.toolhead_mapping) > 0:
			result = "Remap toolheads:\n" 
			for toolhead_map in self.toolhead_mapping:
				result += "Toolhead: " + ",".join(str(i) for i in toolhead_map) + "\n"
			self.gcode.respond_raw(result)
			return
		result = "Toolhead remapping deactivated!\n\n"
		result += "Deactivate remapping with:\n"
		result += "REMAP_TOOLHEADS TOOLHEADS=\n\n"
		result += "Remap toolhead 1 -> 2 with:\n"
		result += "REMAP_TOOLHEADS TOOLHEADS=1,2\n\n"
		result += "Remap toolhead 0 -> 1 and 2 -> 3 with:\n"
		result += "REMAP_TOOLHEADS TOOLHEADS=0,1\n"
		result += "REMAP_TOOLHEADS TOOLHEADS=2,3\n"
		self.gcode.respond_raw(result)

	#####
	# Events
	#####
	def on_loading_error(self, tool):
		self.select_filament(-1)
		self.gcode.run_script_from_command("_RMMU_ON_FILAMENT_LOADING_ERROR TOOLHEAD=" + str(tool))

	def on_filament_insert(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=0, maxval=self.tool_count)

		# check if insert actions are allowed
		if len(self.parking_t_sensor_endstop) != self.tool_count:
			self.ratos_echo("No automatic filament insert actions available without Tx parking sensors!")
			return

		# sanity check before insert action
		if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Parking sensor T" + str(tool) + " triggered! Can not perform insert action.")
			return
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0) and self.start_print_param != None:
			self.ratos_echo("Toolhead Filament sensor triggered! Can not perform insert action.")
			return

		# echo
		self.ratos_echo("Loading filament T" + str(tool) + " into RMMU device...")

		# home if needed
		if not self.is_homed:
			self.home()

		# select filament
		self.select_filament(tool)

		# enable parking sensor endstop
		self.set_pulley_endstop(self.parking_t_sensor_endstop[tool])

		# try to load filament into Tx parking sensor
		step_distance = 25
		max_step_count = 10
		for i in range(max_step_count):
			self.stepper_homing_move(self.rmmu_pulley, step_distance, self.filament_homing_speed, self.filament_homing_accel, 2)
			if self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
				break

		# check sensor
		if not self.is_endstop_triggered(self.parking_t_sensor_endstop[tool]):
			self.ratos_echo("Could not load filament T" + str(tool) + " into the RMMU device! Please load it manually.")
			self.select_filament(-1)
			return

		# move filament to its final parking position
		if not self.unload_filament_from_tx_parking_sensor_to_parking_position(tool):
			return

		# auto load filament into toolhead in case of a ongoing print
		if self.start_print_param != None:
			if self.pause_resume.is_paused and self.runout_detected:
				# move filament into the toolhead sensor
				if not self.load_filament_from_parking_sensor_to_toolhead_sensor(tool):
					return

				# retract filament from the toolhead sensor bc filament loading expects it to be at this position
				if not self.unload_filament_from_toolhead_sensor_to_reverse_bowden(tool):
					return

				# load filament
				if not self.load_filament(tool):
					return

				# run after filament insert macro
				self.gcode.run_script_from_command('_RMMU_AFTER_FILAMENT_INSERT TOOLHEAD=' + str(tool))

		# reset runout detection
		self.runout_detected = False

		# release idler
		self.select_filament(-1)

		# success
		self.ratos_echo("Filament T" + str(tool) + " loaded!")

	def on_filament_runout(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=0, maxval=self.tool_count)
		clogged = param.get('CLOGGED', "true")

		# set runout detection
		self.runout_detected = True

		# run before runout macro
		self.gcode.run_script_from_command('_RMMU_BEFORE_FILAMENT_RUNOUT TOOLHEAD=' + str(tool) + ' CLOGGED=' + str(clogged))

		# unload filament and eject it if no clog has been detected
		if clogged != "true":

			# unload filament
			loaded_filament = self.get_setting(self.VARS_LOADED_FILAMENT)
			if not self.unload_filament(loaded_filament):
				# echo
				self.ratos_echo("Can not eject filament because it couldnt be unloaded!")

				# release idler
				self.select_filament(-1)

				# stop
				return
			
			# eject filament
			self.eject_filaments(tool)

			# check spool joining
			if len(self.spool_joins) > 0:
				for spool_join in self.spool_joins:
					for joined_spool in spool_join:
						if joined_spool == tool:
							if self.join_spool(spool_join, tool):
								self.gcode.run_script_from_command('_RMMU_AFTER_FILAMENT_INSERT TOOLHEAD=' + str(tool))
								return

			# echo
			self.ratos_echo("Load new filament T" + str(tool) + " into the hotend and resume the print!")

	#####
	# Endstop handling
	#####
	def set_pulley_endstop(self, endstop):
		self._unregister_endstop()
		self._register_endstop(endstop)

	def _unregister_endstop(self):
		remove_id = -1
		query_endstops = self.printer.load_object(self.config, 'query_endstops')
		for i in range(0, len(query_endstops.endstops)):
			if query_endstops.endstops[i][1] == "manual_stepper rmmu_pulley":
				remove_id = i
				break
		if remove_id != -1:
			query_endstops.endstops.pop(remove_id)
			self.rmmu_pulley.rail.endstop_map = {}
			self.rmmu_pulley.rail.endstops.pop(0)

	def _register_endstop(self, endstop):
		query_endstops = self.printer.load_object(self.config, 'query_endstops')
		query_endstops.register_endstop(endstop, "manual_stepper rmmu_pulley")
		self.rmmu_pulley.rail.endstops.append((endstop, "manual_stepper rmmu_pulley"))
		endstop.add_stepper(self.rmmu_pulley.get_steppers()[0])

	def query_sensors(self):
		self.gcode.respond_raw("querying RMMU sensors...")
		result = "RMMU Sensors:\n"
		if self.toolhead_filament_sensor_t0 != None:
			result += "Toolhead sensor triggered: " + str(self.is_sensor_triggered(self.toolhead_filament_sensor_t0)) + "\n"
		if len(self.feeder_filament_sensors) == self.tool_count:
			for i in range(0, self.tool_count):
				result += "Feeder sensor T" + str(i) + " triggered: " + str(self.is_sensor_triggered(self.feeder_filament_sensors[i])) + "\n"
		result += "\nRMMU Endstops:\n"
		if self.toolhead_sensor_endstop != None:
			result += "Toolhead endstop triggered: " + str(self.is_endstop_triggered(self.toolhead_sensor_endstop)) + "\n"
		if self.parking_sensor_endstop != None:
			result += "Parking endstop triggered: " + str(self.is_endstop_triggered(self.parking_sensor_endstop)) + "\n"
		if len(self.parking_t_sensor_endstop) == self.tool_count:
			for i in range(0, self.tool_count):
				result += "Parking endstop T" + str(i) + " triggered: " + str(self.is_endstop_triggered(self.parking_t_sensor_endstop[i])) + "\n"
		self.gcode.respond_raw(result)

	#####
	# Helper
	#####
	def move_filament(self, param):
		# parameter
		tool = param.get_int('TOOLHEAD', None, minval=0, maxval=self.tool_count)
		move = param.get_int('MOVE', 50)
		speed = param.get_int('SPEED', 10)
		accel = param.get_int('ACCEL', 100)
		sync = param.get_int('SYNC_EXTRUDER', None, minval=0, maxval=1)

		# home if needed
		if not self.is_homed:
			self.home()

		# select idler
		self.select_filament(tool)

		# move 
		self.rmmu_pulley.do_set_position(0.0)
		if sync == 1:
			self.stepper_synced_move(move, speed, accel)
		else:
			self.stepper_move(self.rmmu_pulley, move, True, speed, accel)

		# release idler
		self.select_filament(-1)

	def calibrate_reverse_bowden_length(self):
		if self.is_sensor_triggered(self.toolhead_filament_sensor_t0):
			self.ratos_echo("No calibration possible! Filament in hotend detected.")
			return

		if len(self.parking_t_sensor_endstop) != self.tool_count:
			self.ratos_echo("No calibration possible! Parking Tx endstops not available.")
			return

		for i in range(0, self.tool_count):
			if not self.is_endstop_triggered(self.parking_t_sensor_endstop[i]):
				self.ratos_echo("No calibration possible! Filament T" + str(i) + " is missing.")
				return

		# echo
		self.ratos_echo("Calibrating, please wait...")

		# home if needed
		if not self.is_homed:
			self.home()

		calibrated_reverse_bowden_length = []
		for i in range(0, self.tool_count):
			if self.is_endstop_triggered(self.parking_t_sensor_endstop[i]):
				# select filament
				self.select_filament(i)
					
				# enable Tx endstop
				self.set_pulley_endstop(self.parking_t_sensor_endstop[i])

				# try to home to the Tx endstop
				step_distance = 100
				max_step_count = 10
				for m in range(max_step_count):
					self.stepper_homing_move(self.rmmu_pulley, -step_distance, self.filament_homing_speed, self.filament_homing_accel, -2)
					if not self.is_endstop_triggered(self.parking_t_sensor_endstop[i]):
						break
				
				# check Tx endstop
				if self.is_endstop_triggered(self.parking_t_sensor_endstop[i]):
					self.ratos_echo("Can not calibrate reverse bowden length bc the filament could not be loaded into its parking sensor!")
					self.select_filament(-1)
					return
					
				# set pulley position
				self.rmmu_pulley.do_set_position(0.0)

				# enable toolhead endstop
				self.set_pulley_endstop(self.toolhead_sensor_endstop)

				# try to home to the toolhead endstop
				step_distance = 10
				max_step_count = 100
				for m in range(max_step_count):
					self.stepper_homing_move(self.rmmu_pulley, step_distance, self.filament_homing_speed, self.filament_homing_accel, 2)
					if self.is_endstop_triggered(self.toolhead_sensor_endstop):
						# get reverse bowden length
						calibrated_reverse_bowden_length.append(m * step_distance)
						self.ratos_echo("Reverse bowden length T" + str(i) + " = " + str(calibrated_reverse_bowden_length[i]) + "mm")
						break

				# check toolhead endstop
				if not self.is_endstop_triggered(self.toolhead_sensor_endstop):
					self.ratos_echo("Can not calibrate reverse bowden length bc the filament could not be loaded into the toolhead sensor!")
					self.select_filament(-1)
					return

				# move filament from toolhead sensor to reverse bowden
				if not self.unload_filament_from_toolhead_sensor_to_reverse_bowden(i):
					self.select_filament(-1)
					return

		# release filament
		self.select_filament(-1)

		if len(calibrated_reverse_bowden_length) == self.tool_count:
			result = 0
			for i in range(self.tool_count):
				result += calibrated_reverse_bowden_length[i]
			result = result / self.tool_count
			self.ratos_echo("Average reverse bowden length = " + str(result) + "mm")

			# save reverse bowden length
			self.set_setting(self.VARS_REVERSE_BOWDEN_LENGTH, result)
			self.ratos_echo("Setting has been saved and activated!")

	def ratos_echo(self, msg):
		self.gcode.run_script_from_command("RATOS_ECHO PREFIX='RMMU' MSG='" + str(msg) + "'")

	def ratos_debug_echo(self, prefix, msg):
		self.gcode.run_script_from_command("DEBUG_ECHO PREFIX='" + str(prefix) + "' MSG='" + str(msg) + "'")

	def stepper_synced_move(self, move, speed, accel=-1):
		if accel == -1:
			accel = self.toolhead.max_accel
		self.gcode.run_script_from_command('G92 E0')
		self.gcode.run_script_from_command('MANUAL_STEPPER STEPPER=rmmu_pulley SET_POSITION=0 MOVE=' + str(move) + ' SPEED=' + str(speed) + ' ACCEL=' + str(accel) + ' SYNC=0')
		self.gcode.run_script_from_command('G0 E' + str(move) + ' F' + str(speed * 60))
		self.gcode.run_script_from_command('MANUAL_STEPPER STEPPER=rmmu_pulley SYNC=1')
		self.rmmu_pulley.do_set_position(0.0)
		self.toolhead.wait_moves()      

	def stepper_move(self, stepper, dist, wait, speed, accel):
		stepper.do_move(dist, speed, accel, True)
		if wait:
			self.toolhead.wait_moves()      

	def stepper_homing_move(self, stepper, dist, speed, accel, homing_move):
		stepper.do_set_position(0.0)
		stepper.do_homing_move(dist, speed, accel, homing_move > 0, abs(homing_move) == 1)
		self.toolhead.wait_moves()      

	def is_endstop_triggered(self, endstop):
		return bool(endstop.query_endstop(self.toolhead.get_last_move_time()))     

	def is_sensor_triggered(self, sensor):
		return bool(sensor.runout_helper.filament_present)     

	def extruder_get_temperature(self):
		return self.heater.get_status(self.toolhead.get_last_move_time())['temperature']

	def extruder_get_target_temperature(self):
		return self.heater.get_status(self.toolhead.get_last_move_time())['target']

	def extruder_set_temperature(self, temperature, wait):
		self.pheaters.set_temperature(self.heater, temperature, wait)

	def extruder_can_extrude(self):
		status = self.extruder.get_status(self.toolhead.get_last_move_time())
		result = status['can_extrude'] 
		return result
	
#####
# Entry Point
#####
def load_config(config):
	return RMMU(config)
