from gettext import ngettext
from pathlib import Path
from typing import Any, Dict, Optional

from gi.repository import Gdk, GObject, Gtk

from gourmand.convert import frac_to_float
from gourmand.gtk_extras import WidgetSaver, mnemonic_manager, pageable_store
from gourmand.gtk_extras import cb_extras as cb
from gourmand.gtk_extras import dialog_extras as de
from gourmand.i18n import _

from . import keyEditorPluggable


class KeyEditor:
    """KeyEditor sets up a GUI to allow editing which keys correspond to which
    items throughout the recipe database.
    It is useful for corrections or changes to keys en masse.
    """

    def __init__(self, rd=None, rg=None):
        self.ui = Gtk.Builder()
        self.ui.add_from_file(str(Path(__file__).parent / "keyeditor.ui"))
        self.rd = rd
        self.rg = rg
        self.widget_names = [
            "treeview",
            "searchByBox",
            "searchEntry",
            "searchButton",
            "window",
            "searchAsYouTypeToggle",
            "regexpTog",
            "changeKeyEntry",
            "changeItemEntry",
            "changeUnitEntry",
            "changeAmountEntry",
            "applyEntriesButton",
            "clearEntriesButton",
        ]
        for w in self.widget_names:
            setattr(self, w, self.ui.get_object(w))
        self.entries = {
            "ingkey": self.changeKeyEntry,
            "item": self.changeItemEntry,
            "unit": self.changeUnitEntry,
            "amount": self.changeAmountEntry,
        }
        # setup entry callback to sensitize/desensitize apply
        self.applyEntriesButton.set_sensitive(False)
        self.clearEntriesButton.set_sensitive(False)
        for e in list(self.entries.values()):
            e.connect("changed", self.entryChangedCB)
        # Make our lovely model
        self.makeTreeModel()
        # setup completion in entry
        model = Gtk.ListStore(str)
        for k in self.rd.get_unique_values("ingkey", table=self.rd.ingredients_table):
            model.append([k])
        cb.make_completion(self.changeKeyEntry, model)
        # Setup next/prev/first/last buttons for view
        self.prev_button = self.ui.get_object("prevButton")
        self.next_button = self.ui.get_object("nextButton")
        self.first_button = self.ui.get_object("firstButton")
        self.last_button = self.ui.get_object("lastButton")
        self.showing_label = self.ui.get_object("showingLabel")
        self.prev_button.connect("clicked", lambda *args: self.treeModel.prev_page())
        self.next_button.connect("clicked", lambda *args: self.treeModel.next_page())
        self.first_button.connect("clicked", lambda *args: self.treeModel.goto_first_page())
        self.last_button.connect("clicked", lambda *args: self.treeModel.goto_last_page())
        # Setup search stuff
        self.search_string = ""
        self.search_by = _("key")
        self.use_regexp = True
        self.setupTreeView()
        self.treeview.set_model(self.treeModel)
        self.treeview.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        # self.treeview.set_model(self.treeModel)
        self.ui.connect_signals(
            {
                "iSearch": self.isearchCB,
                "search": self.searchCB,
                "search_as_you_type_toggle": self.search_as_you_typeCB,
                "applyEntries": self.applyEntriesCB,
                "clearEntries": self.clearEntriesCB,
                "close_window": lambda *args: self.window.hide(),
                #'editNutritionalInfo':self.editNutritionalInfoCB,
            }
        )
        # setup mnemonic manager
        self.mm = mnemonic_manager.MnemonicManager()
        self.mm.sacred_cows.append("search for")
        self.mm.add_builder(self.ui)
        self.mm.add_treeview(self.treeview)
        self.mm.fix_conflicts_peacefully()
        # to set our regexp_toggled variable
        cb.set_model_from_list(self.searchByBox, [_("key"), _("item"), _("unit")])
        self.searchByBox.set_active(0)
        self.dont_ask = self.rg.prefs.get("dontAskDeleteKey", False)
        # setup WidgetSavers
        self.rg.conf.append(
            WidgetSaver.WidgetSaver(self.searchAsYouTypeToggle, self.rg.prefs.get("sautTog", {"active": self.searchAsYouTypeToggle.get_active()}), ["toggled"])
        )
        self.rg.conf.append(WidgetSaver.WidgetSaver(self.regexpTog, self.rg.prefs.get("regexpTog", {"active": self.regexpTog.get_active()}), ["toggled"]))

    def dont_ask_cb(self, widget, *args):
        self.dont_ask = widget.get_active()
        self.rg.prefs["dontAskDeleteKey"] = self.dont_ask

    def setupTreeView(self):
        self.FIELD_COL = 1
        self.VALUE_COL = 2
        self.COUNT_COL = 3
        self.REC_COL = 4
        self.NUT_COL = 5
        for n, head in [
            [self.FIELD_COL, _("Field")],
            [self.VALUE_COL, _("Value")],
            [self.COUNT_COL, _("Count")],
            [self.REC_COL, _("Recipes")],
            # [self.NUT_COL, _('Nutritional Info')],
        ]:
            if n == self.NUT_COL:
                renderer = Gtk.CellRendererToggle()
            else:
                renderer = Gtk.CellRendererText()
            # If we have gtk > 2.8, set up text-wrapping
            try:
                renderer.get_property("wrap-width")
            except TypeError:
                pass
            else:
                renderer.set_property("wrap-mode", Gtk.WrapMode.WORD)
                if n == self.FIELD_COL:
                    renderer.set_property("wrap-width", 60)
                elif n in [self.VALUE_COL, self.REC_COL]:
                    renderer.set_property("wrap-width", 250)
                else:
                    renderer.set_property("wrap-width", 100)
            if n == self.VALUE_COL:
                renderer.set_property("editable", True)
                renderer.connect("edited", self.tree_edited, n, head)
            if n == self.NUT_COL:
                col = Gtk.TreeViewColumn(head, renderer, active=n, visible=n)
            else:
                col = Gtk.TreeViewColumn(head, renderer, text=n)
            if n == self.VALUE_COL:
                col.set_property("expand", True)
            col.set_resizable(True)
            self.treeview.append_column(col)
        plugin_manager = keyEditorPluggable.get_key_editor_plugin_manager()
        for tvc in plugin_manager.get_treeview_columns(None, key_col=2, instant_apply=True):
            self.treeview.append_column(tvc)

    def tree_edited(self, renderer, path_string, text, n, head):
        indices = path_string.split(":")
        path = tuple(map(int, indices))
        itr = self.treeModel.get_iter(path)
        curdic, field = self.get_dic_describing_iter(itr)
        value = curdic[field]
        if value == text:
            return
        if field == "ingkey":
            key = curdic["ingkey"]
            if de.getBoolean(
                label=_('Change all keys "%s" to "%s"?') % (key, text),
                sublabel=_(
                    "You won't be able to undo this action. If there are already ingredients with the key \"%s\", you won't be able to distinguish between those items and the items you are changing now."  # noqa: E501
                )
                % text,
            ):
                self.rd.update_by_criteria(self.rd.ingredients_table, curdic, {"ingkey": text})
                self.rd.delete_by_criteria(self.rd.keylookup_table, {"ingkey": key})
        elif field == "item":
            if de.getBoolean(
                label=_('Change all items "%s" to "%s"?') % (curdic["item"], text),
                sublabel=_(
                    "You won't be able to undo this action. If there are already ingredients with the item \"%s\", you won't be able to distinguish between those items and the items you are changing now."  # noqa: E501
                )
                % text,
            ):
                self.rd.update_by_criteria(self.rd.ingredients_table, curdic, {"item": text})
        elif field == "unit":
            unit = curdic["unit"]
            key = curdic["ingkey"]
            item = curdic["item"]
            val = de.getRadio(
                label="Change unit",
                options=[
                    [_('Change _all instances of "%(unit)s" to "%(text)s"') % locals(), 1],
                    [_('Change "%(unit)s" to "%(text)s" only for _ingredients "%(item)s" with key "%(key)s"') % locals(), 2],
                ],
                default=2,
            )
            if val == 1:
                self.rd.update_by_criteria(
                    self.rd.ingredients_table,
                    {"unit": unit},
                    {"unit": text},
                )
            elif val == 2:
                self.rd.update_by_criteria(self.rd.ingredients_table, curdic, {"unit": text})
        elif field == "amount":
            amount = curdic["amount"]
            unit = curdic["unit"]
            key = curdic["ingkey"]
            item = curdic["item"]
            try:
                new_amount = frac_to_float(text)
            except Exception:
                de.show_amount_error(text)
                return
            val = de.getRadio(
                label="Change amount",
                options=[
                    [_('Change _all instances of "%(amount)s" %(unit)s to %(text)s %(unit)s') % locals(), 1],
                    [_('Change "%(amount)s" %(unit)s to "%(text)s" %(unit)s only _where the ingredient key is %(key)s') % locals(), 2],
                    [
                        _('Change "%(amount)s" %(unit)s to "%(text)s" %(unit)s only where the ingredient key is %(key)s _and where the item is %(item)s')
                        % locals(),
                        3,
                    ],
                ],
                default=3,
            )
            if val == 1:
                cond = {"unit": unit, "amount": amount}
            elif val == 2:
                cond = {"unit": unit, "amount": amount, "ingkey": key}
            elif val == 3:
                cond = curdic
            self.rd.update_by_criteria(self.rd.ingredients_table, {"unit": unit, "amount": frac_to_float(amount)}, {"unit": unit, "amount": new_amount})
        else:
            return
        self.treeModel.set_value(itr, n, text)
        return

    def makeTreeModel(self):
        self.treeModel = KeyStore(self.rd, per_page=self.rg.prefs.get("recipes_per_page", 12))
        # self.orig_view = self.treeModel.view
        self.treeModel.connect("page-changed", self.model_changed_cb)
        self.treeModel.connect("view-changed", self.model_changed_cb)

    def resetTree(self):
        self.search_string = "NO ONE WOULD EVER SEARCH FOR THIS HACKISH STRING"
        curpage = self.treeModel.page
        self.do_search()
        self.treeModel.set_page(curpage)

    def do_search(self):
        last_search = self.search_string
        self.search_string = self.searchEntry.get_text()
        last_by = self.search_by
        self.treeModel.search_by = self.search_by = cb.cb_get_active_text(self.searchByBox)
        last_regexp = self.use_regexp
        self.use_regexp = self.regexpTog.get_active()

        # Do nothing if this callback was called with no changes
        if self.search_by == last_by and self.search_string == last_search and self.use_regexp == last_regexp:
            return

        # Clear the view if searching for something different
        if not self.search_string.startswith(last_search) or self.search_by != last_by or self.use_regexp != last_regexp:
            self.treeModel.reset_views()

        if self.search_by == _("item"):
            column = "item"
        elif self.search_by == _("key"):
            column = "ingkey"
        else:  # self.search_by == _('unit'):
            column = "unit"

        self.treeModel.limit(self.search_string, column, search_options={"use_regexp": self.use_regexp})

    def isearchCB(self, *args):
        if self.searchAsYouTypeToggle.get_active():
            self.window.get_window().set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
            GObject.idle_add(lambda *args: (self.do_search() or self.window.get_window().set_cursor(None)))

    def searchCB(self, *args):
        self.do_search()

    def search_as_you_typeCB(self, *args):
        if self.searchAsYouTypeToggle.get_active():
            self.searchButton.hide()
        else:
            self.searchButton.show()

    def clearEntriesCB(self, *args):
        for e in list(self.entries.values()):
            e.set_text("")

    def get_dic_describing_iter(self, itr):
        """Handed an itr in our tree, return a dictionary describing
        that row and the field described.

        For example, if we get the row

        KEY: Foo

        We return {'ingkey':'Foo'},'ingkey'

        If we get the row

        KEY: Foo
         |=> ITEM: Bar

        We return {'ingkey':'Foo','item':'Bar'},'item'
        """
        field = self.treeModel.get_value(itr, self.FIELD_COL)
        value = self.treeModel.get_value(itr, self.VALUE_COL)
        if field == self.treeModel.KEY:
            return {"ingkey": value}, "ingkey"
        elif field == self.treeModel.ITEM:
            key = self.treeModel.get_value(self.treeModel.iter_parent(itr), self.VALUE_COL)
            return {"ingkey": key, "item": value}, "item"
        elif field == self.treeModel.UNIT:
            item_itr = self.treeModel.iter_parent(itr)
            key_itr = self.treeModel.iter_parent(item_itr)
            item = self.treeModel.get_value(item_itr, self.VALUE_COL)
            key = self.treeModel.get_value(key_itr, self.VALUE_COL)
            unit = value
            return {"ingkey": key, "item": item, "unit": unit}, "unit"
        elif field == self.treeModel.AMOUNT:
            unit_itr = self.treeModel.iter_parent(itr)
            item_itr = self.treeModel.iter_parent(unit_itr)
            key_itr = self.treeModel.iter_parent(item_itr)
            unit = self.treeModel.get_value(unit_itr, self.VALUE_COL)
            item = self.treeModel.get_value(item_itr, self.VALUE_COL)
            key = self.treeModel.get_value(key_itr, self.VALUE_COL)
            amount = value
            return {"ingkey": key, "item": item, "unit": unit, "amount": amount}, "amount"
        else:
            print("WTF! WE SHOULD NEVER LAND HERE!", field, value)
            raise Exception("WTF ERROR")

    def applyEntriesCB(self, *args):
        newdic = {}
        for k, e in list(self.entries.items()):
            txt = e.get_text()
            if txt:
                if k == "amount":
                    try:
                        newdic[k] = frac_to_float(txt)
                    except ValueError:
                        print("Problem with amount:", txt)
                        import traceback

                        traceback.print_exc()
                        de.show_amount_error(txt)
                        return
                else:
                    newdic[k] = txt
        if not newdic:
            print("We called applyEntriesCB with no text -- that shouldn't be possible")
            return
        mod, rows = self.treeview.get_selection().get_selected_rows()
        if not de.getBoolean(
            label=_("Change all selected rows?"),
            sublabel=(
                _("This action will not be undoable. Are you that for all %s selected rows, you want to set the following values:") % len(rows)
                + ("ingkey" in newdic and _("\nKey to %s") % newdic["ingkey"] or "")
                + ("item" in newdic and _("\nItem to %s") % newdic["item"] or "")
                + ("unit" in newdic and _("\nUnit to %s") % newdic["unit"] or "")
                + ("amount" in newdic and _("\nAmount to %s") % newdic["amount"] or "")
            ),
        ):
            return
        # Now actually apply our lovely new logic...
        updated_iters = []
        for path in rows:
            itr = self.treeModel.get_iter(path)
            # We check to see if we've updated the parent of our iter,
            # in which case the changes would already be inherited by
            # the current row (i.e. if the tree has been expanded and
            # all rows have been selected).
            parent = mod.iter_parent(itr)
            already_updated = False
            while parent:
                if parent in updated_iters:
                    already_updated = True
                else:
                    parent = mod.iter_parent(parent)
            if already_updated:
                continue
            # Now that we're sure we really need to update...
            curdic, field = self.get_dic_describing_iter(itr)
            # curkey = self.treeModel.get_value(itr, self.VALUE_COL)
            if not already_updated:
                self.rd.update_by_criteria(
                    self.rd.ingredients_table,
                    curdic,
                    newdic,
                )
                if "ingkey" in curdic and "ingkey" in newdic:
                    self.rd.delete_by_criteria(self.rd.keylookup_table, {"ingkey": curdic["ingkey"]})
        self.resetTree()

    def editNutritionalInfoCB(self, *args):
        from ..nutritional_information import nutritionDruid

        nid = nutritionDruid.NutritionInfoDruid(self.rg.nd, self.rg.prefs)
        mod, rows = self.treeview.get_selection().get_selected_rows()
        keys_to_update = {}
        for path in rows:
            itr = mod.get_iter(path)
            # Climb to the key-level for each selection -- we don't
            # care about anything else.
            parent = mod.iter_parent(itr)
            while parent:
                itr = parent
                parent = mod.iter_parent(itr)
            curkey = mod.get_value(itr, self.VALUE_COL)
            # if mod.get_value(itr,self.NUT_COL):
            #    print "We can't yet edit nutritional information..."
            # else:
            if True:
                keys_to_update[curkey] = []
                child = mod.iter_children(itr)
                while child:
                    grandchild = mod.iter_children(child)
                    while grandchild:
                        # Grand children are units...
                        unit = mod.get_value(grandchild, self.VALUE_COL)
                        greatgrandchild = mod.iter_children(grandchild)
                        while greatgrandchild:
                            amount = mod.get_value(greatgrandchild, self.VALUE_COL)
                            keys_to_update[curkey].append((frac_to_float(amount), unit))
                            greatgrandchild = mod.iter_next(greatgrandchild)
                        grandchild = mod.iter_next(grandchild)
                    child = mod.iter_next(child)
                nid.add_ingredients(list(keys_to_update.items()))
                nid.connect("finish", self.update_nutinfo)
                nid.show()

    def update_nutinfo(self, *args):
        self.treeModel.reset_views()

    def update_iter(self, itr, newdic):
        """Update iter and its children based on values in newdic"""
        field = self.treeModel.get_value(itr, self.FIELD_COL)
        if "item" in newdic and field == self.treeModel.ITEM:
            self.treeModel.set_value(itr, self.VALUE_COL, newdic["item"])
        elif "ingkey" in newdic and field == self.treeModel.KEY:
            self.treeModel.set_value(itr, self.VALUE_COL, newdic["ingkey"])
        elif "unit" in newdic and field == self.treeModel.UNIT:
            self.treeModel.set_value(itr, self.VALUE_COL, newdic["unit"])
        elif "amount" in newdic and field == self.treeModel.AMOUNT:
            self.treeModel.set_value(itr, self.VALUE_COL, newdic["amount"])
        c = self.treeModel.iter_children(itr)
        while c:
            self.update_iter(c, newdic)
            c = self.treeModel.iter_next(c)

    def entryChangedCB(self, *args):
        """Set sensitivity of apply and clear buttons.

        We are sensitive if we have text to apply or clear"""
        for e in list(self.entries.values()):
            if e.get_text():
                self.applyEntriesButton.set_sensitive(True)
                self.clearEntriesButton.set_sensitive(True)
                return
        self.applyEntriesButton.set_sensitive(False)
        self.clearEntriesButton.set_sensitive(False)

    def reset_tree(self):
        self.treeModel.reset_views()
        self.search_by = None
        self.search_string = ""
        self.do_search()

    # Paging handlers
    def model_changed_cb(self, model):
        if model.page == 0:
            self.prev_button.set_sensitive(False)
            self.first_button.set_sensitive(False)
        else:
            self.prev_button.set_sensitive(True)
            self.first_button.set_sensitive(True)
        if model.get_last_page() == model.page:
            self.next_button.set_sensitive(False)
            self.last_button.set_sensitive(False)
        else:
            self.next_button.set_sensitive(True)
            self.last_button.set_sensitive(True)
        self.update_showing_label()

    def update_showing_label(self):
        bottom, top, total = self.treeModel.showing()
        if top >= total and bottom == 1:
            lab = ngettext("%s ingredient", "%s ingredients", top) % top
        else:
            # Do not translate bottom, top and total -- I use these fancy formatting
            # strings in case your language needs the order changed!
            lab = _("Showing ingredients %(bottom)s to %(top)s of %(total)s" % locals())
        self.showing_label.set_markup("<i>" + lab + "</i>")


class KeyStore(pageable_store.PageableTreeStore, pageable_store.PageableViewStore):
    """A ListStore to show our beautiful keys."""

    __gsignals__ = {
        "view-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    KEY = _("Key") + ":"
    ITEM = _("Item") + ":"
    UNIT = _("Unit") + ":"
    AMOUNT = _("Amount") + ":"

    columns = ["obj", "ingkey", "item", "count", "recipe"]  # ,'ndbno']

    def __init__(self, rd, per_page=15):
        self.__last_limit_text = ""
        self.rd = rd
        pageable_store.PageableTreeStore.__init__(
            self,
            [
                GObject.TYPE_PYOBJECT,  # row ref
                str,  # column
                str,  # value
                int,  # count
                str,  # recipe
                # int, # nutritional information equivalent
            ],
            per_page=per_page,
        )

    def reset_views(self):
        if self.__last_limit_text:
            txt = self.__last_limit_text
            if hasattr(self, "use_regexp") and self.use_regexp:
                s = {"search": txt, "operator": "REGEXP"}
            else:
                s = {"search": "%" + txt.replace("%", "%%") + "%", "operator": "LIKE"}
            if self.search_by == _("item"):
                s["column"] = "item"
            else:
                s["column"] = "ingkey"
            self.view = self.rd.get_ingkeys_with_count(s)
        else:
            self.view = self.rd.get_ingkeys_with_count()
        for n in range(self._get_length_()):
            parent = (n,)
            path = parent
            try:
                itr = self.get_iter(path)
            except ValueError:
                return
            self.emit("row-changed", Gtk.TreePath.new_from_indices(path), itr)
            child = self.iter_children(itr)
            while child:
                path = self.get_path(child)
                self.emit("row-changed", path, child)
                child = self.iter_next(child)
        # self.keylookup_table = self.rd.filter(self.rd.keylookup_table,lambda row: row.item)
        # Limit ingredients_table to ingkeys only, then select the unique values of that, then
        # filter ourselves to values that have keys
        # self.view = self.rd.filter(self.rd.ingredients_table.project(self.rd.ingredients_table.ingkey).unique(),
        #                           lambda foo: foo.ingkey)

    def _setup_parent_(self, *args, **kwargs):
        self.reset_views()

    def limit(self, txt: str, column: str, search_options: Optional[Dict[str, Any]] = None):
        if search_options is None:
            search_options = {}
        if txt == self.__last_limit_text:
            return
        else:
            self.__last_limit_text = txt
        if not txt:
            self.reset_views()
        if search_options["use_regexp"]:
            s = {"search": txt, "operator": "REGEXP"}
        else:
            s = {"search": "%" + txt.replace("%", "%%") + "%", "operator": "LIKE"}
        s["column"] = column
        self.change_view(self.rd.get_ingkeys_with_count(s))

    def _get_length_(self):
        return len(self.view)

    get_last_page = pageable_store.PageableViewStore.get_last_page

    def _get_slice_(self, bottom, top):
        return [self.get_row(i) for i in self.view[bottom:top]]

    def _get_item_(self, path):
        # TODO: Not called? Where does `indx` come from?
        return self.get_row(self.view[indx])  # noqa: F821

    def get_row(self, row):
        return [
            row,
            self.KEY,
            row.ingkey,
            # avoidable slowdown (look here if code seems sluggish)
            row.count,
            None,
            # row.ndbno or 0,
        ]

    def _get_children_(self, itr):
        ret = []
        field = self.get_value(itr, 1)
        value = self.get_value(itr, 2)
        if field == self.KEY:
            ingkey = value
            for item in self.rd.get_unique_values("item", self.rd.ingredients_table, ingkey=ingkey):
                ret.append(
                    [
                        None,
                        self.ITEM,
                        item,
                        self.rd.fetch_len(self.rd.ingredients_table, ingkey=ingkey, item=item),
                        self.get_recs(ingkey, item),
                        # 0
                    ]
                )
        elif field == self.ITEM:
            ingkey = self.get_value(self.iter_parent(itr), 2)
            item = value
            for unit in self.rd.get_unique_values("unit", self.rd.ingredients_table, ingkey=ingkey, item=item):
                ret.append(
                    [
                        None,
                        self.UNIT,
                        unit,
                        self.rd.fetch_len(self.rd.ingredients_table, ingkey=ingkey, item=item, unit=unit),
                        None,
                    ]
                )
            if not ret:
                ret.append(
                    [
                        None,
                        self.UNIT,
                        "",
                        self.get_value(self.iter_parent(itr), 3),
                        None,
                    ]
                )
        elif field == self.UNIT:
            item = self.get_value(self.iter_parent(itr), 2)
            ingkey = self.get_value(self.iter_parent(self.iter_parent(itr)), 2)
            unit = self.get_value(itr, 2)
            amounts = []
            for i in self.rd.fetch_all(self.rd.ingredients_table, ingkey=ingkey, item=item, unit=unit):
                astring = self.rd.get_amount_as_string(i)
                if astring in amounts:
                    continue
                ret.append(
                    [
                        None,
                        self.AMOUNT,
                        astring,
                        (
                            i.rangeamount
                            and self.rd.fetch_len(self.rd.ingredients_table, ingkey=ingkey, item=item, unit=unit, amount=i.amount, rangeamount=i.rangeamount)
                            or self.rd.fetch_len(self.rd.ingredients_table, ingkey=ingkey, item=item, unit=unit, amount=i.amount)
                        ),
                        None,
                    ]
                )
                amounts.append(astring)
            if not ret:
                ret.append(
                    [
                        None,
                        self.AMOUNT,
                        "",
                        self.get_value(self.iter_parent(itr), 3),
                        None,
                    ]
                )

        return ret

    def get_recs(self, key, item) -> str:
        """Return a string with a list of recipes containing an ingredient with
        key and item"""
        recs = [i.recipe_id for i in self.rd.fetch_all(self.rd.ingredients_table, ingkey=key, item=item)]
        titles = []
        looked_at = []
        for r_id in recs:
            if r_id in looked_at:
                continue
            rec = self.rd.get_rec(r_id)
            if rec:
                titles.append(rec.title)
        return ", ".join(titles)
