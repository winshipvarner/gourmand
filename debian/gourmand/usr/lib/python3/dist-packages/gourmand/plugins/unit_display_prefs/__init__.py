from gi.repository import Gtk

from gourmand.i18n import _
from gourmand.plugin import DatabasePlugin, ToolPlugin
from gourmand.plugin_loader import POST, PRE
from gourmand.prefs import Prefs
from gourmand.reccard import RecCardDisplay

from . import unit_prefs_dialog


class UnitDisplayPlugin (ToolPlugin):
    menu_items = '''<placeholder name="StandaloneTool">
                    <menuitem action="ShowUnitAdjusterDialog"/>
                  </placeholder>'''
    menu_bars = ['RecipeDisplayMenuBar']
    reccards = []

    def __init__ (self):
        ToolPlugin.__init__(self)

    def activate (self, pluggable):
        if isinstance (pluggable, RecCardDisplay):
            self.reccards.append(pluggable)
            self.add_to_uimanager(pluggable.ui_manager)

    def setup_action_groups (self):
        self.action_group = Gtk.ActionGroup(name='UnitAdjusterActionGroup')
        self.action_group.add_actions([
            ('ShowUnitAdjusterDialog',None,_('Set _unit display preferences'),
             None,_('Automatically convert units to preferred system (metric, imperial, etc.) where possible.'),
             self.show_converter_dialog),
            ])
        self.action_groups.append(self.action_group)

    def show_converter_dialog (self, *args):
        unit_prefs_dialog.UnitPrefsDialog(self.reccards).run()

class UnitDisplayDatabasePlugin (DatabasePlugin):

    def activate (self, db):
        if self.active:
            print('Strange -- activate called twice')
            print('Activate plugin',self,db,'from:')
            import traceback; traceback.print_stack()
            print('ignoring')
            return
        self.db = db
        db.add_hook(PRE,'get_amount_and_unit',self.get_amount_and_unit_hook)
        self.active = True

    def get_amount_and_unit_hook (self, db, *args, **kwargs):
        kwargs['preferred_unit_groups'] = Prefs.instance().get('preferred_unit_groups',[])
        return args,kwargs


plugins = [UnitDisplayPlugin, UnitDisplayDatabasePlugin]
