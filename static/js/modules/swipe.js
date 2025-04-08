// static/js/modules/swipe.js

// Swipe Navigation State & Logic

// Elements - These will be assigned from the main app.js after DOMContentLoaded
let contentWrapper = null;
let swipeSections = null; // NodeList of sections
let numSections = 0;

// State Variables - Managed by the handlers, potentially read/updated by main app.js
let currentSectionIndex = 0; // Initial section index
let touchStartX = 0;
let touchStartY = 0;
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false;
const swipeThreshold = 50; // Min pixels for swipe

/**
 * Initializes swipe module by assigning DOM elements and getting section count.
 * Should be called after DOM is loaded in app.js.
 * @param {HTMLElement} wrapper - The main content wrapper element.
 * @param {NodeList} sections - The NodeList of swipeable sections.
 */
export function initSwipe(wrapper, sections) {
    contentWrapper = wrapper;
    swipeSections = sections;
    numSections = sections.length;
    console.log(`Swipe module initialized with ${numSections} sections.`);
    // Set initial transform without transition
    updateContentWrapperTransform(currentSectionIndex, false);
}

/**
 * Sets the current section index directly.
 * Useful for initialization or external control.
 * @param {number} index - The new section index.
 * @param {boolean} [useTransition=true] - Whether to use CSS transition.
 */
export function setCurrentSectionIndex(index, useTransition = true) {
    if (index >= 0 && index < numSections) {
        currentSectionIndex = index;
        updateContentWrapperTransform(currentSectionIndex, useTransition);
    } else {
        console.warn(`Attempted to set invalid section index: ${index}`);
    }
}

/**
 * Gets the current section index.
 * @returns {number} The current section index.
 */
export function getCurrentSectionIndex() {
    return currentSectionIndex;
}


/**
 * TouchStart event handler for swipe detection.
 * @param {TouchEvent} event - The touch event object.
 */
export function handleTouchStart(event) {
    if (!contentWrapper) return; // Not initialized

    const target = event.target;
    // Allow swipe ONLY if the touch starts directly on the wrapper or section background,
    // AND NOT on elements known to scroll or be interactive within a section.
    const isDirectTarget = target === contentWrapper || target.classList.contains('swipe-section') || target.classList.contains('section-content');
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content'); // Check common scrollable containers
    const isModalOpen = document.querySelector('.modal[style*="display: block"]'); // Check if any modal is visible

    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, direct: ${isDirectTarget}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${!!isModalOpen})`);
        return; // Exit if interaction is likely needed or target is wrong
    }

    // Proceed with swipe initialization
    try {
        touchStartX = event.touches[0].clientX;
        touchStartY = event.touches[0].clientY; // Record Y start
        touchCurrentX = touchStartX;
        isSwiping = true;
        horizontalSwipeConfirmed = false; // Reset confirmation flag
        contentWrapper.style.transition = 'none'; // Disable transition during drag
        console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
    } catch (e) {
        console.error("Error in touchstart:", e);
        isSwiping = false; // Ensure swipe is cancelled on error
    }
}

/**
 * TouchMove event handler for swipe detection.
 * @param {TouchEvent} event - The touch event object.
 */
export function handleTouchMove(event) {
    if (!isSwiping || !contentWrapper) return; // Exit if not currently swiping or not initialized

    try {
        const currentY = event.touches[0].clientY;
        touchCurrentX = event.touches[0].clientX;
        const diffX = touchCurrentX - touchStartX;
        const diffY = currentY - touchStartY;

        // Determine dominant direction ONCE per swipe
        if (!horizontalSwipeConfirmed) {
            if (Math.abs(diffX) > Math.abs(diffY) + 5) { // Horizontal movement dominant
                horizontalSwipeConfirmed = true;
                console.log("Horizontal swipe confirmed.");
            } else if (Math.abs(diffY) > Math.abs(diffX) + 5) { // Vertical movement dominant
                isSwiping = false; // Cancel swipe
                console.log("Vertical scroll detected, canceling swipe.");
                return; // Allow default vertical scroll
            }
            // Else: direction not yet clear, continue monitoring
        }

        // Only prevent default and apply transform if horizontal swipe is confirmed
        if (horizontalSwipeConfirmed) {
            event.preventDefault(); // Prevent page scroll during horizontal drag
            const baseTranslateXPercent = -currentSectionIndex * 100;
            // Apply immediate transform feedback based on drag distance
            contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    } catch (e) {
         console.error("Error in touchmove:", e);
         isSwiping = false; // Ensure swipe is cancelled on error
    }
}

/**
 * TouchEnd event handler for swipe detection.
 * @param {TouchEvent} event - The touch event object.
 */
export function handleTouchEnd(event) {
    if (!isSwiping || !contentWrapper) return; // Exit if swipe didn't start/cancel or not initialized

    const wasHorizontal = horizontalSwipeConfirmed; // Store confirmation state
    // Reset flags
    isSwiping = false;
    horizontalSwipeConfirmed = false;

    let finalSectionIndex = currentSectionIndex; // Start with current index

    // Only process section change if horizontal swipe was confirmed
    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (Horizontal): diffX=${diffX.toFixed(0)}, threshold=${swipeThreshold}`);

        // Determine if swipe was significant enough to change section
        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0 && currentSectionIndex < numSections - 1) { // Swipe Left
                finalSectionIndex++; console.log("Swipe Left -> New Index:", finalSectionIndex);
            } else if (diffX > 0 && currentSectionIndex > 0) { // Swipe Right
                finalSectionIndex--; console.log("Swipe Right -> New Index:", finalSectionIndex);
            } else { console.log("Swipe threshold met but at section boundary."); }
        } else { console.log("Swipe distance below threshold."); }

    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Snapping back.");
    }

    // Update the globally managed index (assuming app.js handles this)
    // For modularity, this function could return the new index instead:
    // return finalSectionIndex;
    // For now, we update the internal state and let app.js call updateTransform
    currentSectionIndex = Math.max(0, Math.min(numSections - 1, finalSectionIndex));

    updateContentWrapperTransform(currentSectionIndex, true); // Animate to the final section position
}

/**
 * Updates the CSS transform of the content wrapper to show the correct section.
 * @param {number} index - The index of the section to display.
 * @param {boolean} [useTransition=true] - Whether to use CSS transition for animation.
 */
export function updateContentWrapperTransform(index, useTransition = true) {
    if (contentWrapper && index >= 0 && index < numSections) {
        const newTranslateXPercent = -index * 100;
        console.log(`Updating transform: Index=${index}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-in-out' : 'none';
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
    } else if (contentWrapper) {
         console.warn(`Invalid index (${index}) passed to updateContentWrapperTransform. Current index: ${currentSectionIndex}`);
    } else {
        console.error("contentWrapper not found in updateContentWrapperTransform!");
    }
}

/**
 * Adds keyboard navigation listeners (left/right arrows) for swipe sections.
 * Needs the main app's current index state management.
 * @param {function} getIndex - Function to get the current section index from app.js.
 * @param {function} setIndex - Function to set the new section index in app.js.
 */
export function addKeyboardNavListeners(getIndex, setIndex) {
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = document.querySelector('.modal[style*="display: block"]');
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return; // Ignore if modal open or input focused

        let currentIndex = getIndex(); // Get current index from app.js state

        if (e.key === 'ArrowLeft' && currentIndex > 0) {
            console.log("Key Left -> Previous Section");
            setIndex(currentIndex - 1); // Update index in app.js state
            updateContentWrapperTransform(currentIndex - 1, true); // Apply transform
        } else if (e.key === 'ArrowRight' && currentIndex < numSections - 1) {
             console.log("Key Right -> Next Section");
            setIndex(currentIndex + 1); // Update index in app.js state
            updateContentWrapperTransform(currentIndex + 1, true); // Apply transform
        }
    });
    console.log("Keyboard navigation listeners added.");
}
